"""Campaign-budgeted research catalogue with an append-only record for every candidate."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from .durable import DurableJournal

MAX_VARIANTS_PER_RUN = 256
OUTCOMES = frozenset({"EVALUATED", "FAILED", "SKIPPED", "ABORTED"})
_SHA = frozenset("0123456789abcdef")


class ResearchLibraryError(ValueError):
    pass


def json_safe(value: Any) -> Any:
    """Convert numpy/pandas/date results to canonical JSON without silently accepting NaN/Inf."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else {"$float": "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")}
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if hasattr(value, "item"):
        return json_safe(value.item())
    raise ResearchLibraryError(f"unsupported audit value type: {type(value).__name__}")


def canonical_sha256(value: Any) -> str:
    body = json.dumps(json_safe(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def source_sha256(path: Path) -> str:
    """Hash checkout-independent Python/text content; CRLF is normalized to the Git LF blob."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


class ResearchVariantJournal:
    """Hash-chained journal that cannot close a research run until every declared variant has an outcome."""

    def __init__(self, path: Path, *, max_variants_per_run: int = MAX_VARIANTS_PER_RUN) -> None:
        if not 1 <= max_variants_per_run <= MAX_VARIANTS_PER_RUN:
            raise ResearchLibraryError(f"variant limit must be in [1, {MAX_VARIANTS_PER_RUN}]")
        self.journal = DurableJournal(Path(path))
        self.max_variants_per_run = max_variants_per_run
        self._runs: dict[str, dict] = {}
        self._campaigns: dict[str, dict] = {}
        self._rebuild()

    def _rebuild(self) -> None:
        for row in self.journal.records():
            run_id = row.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                raise ResearchLibraryError("research journal row has no run id")
            if row.get("t") == "RESEARCH_RUN_OPENED":
                if run_id in self._runs:
                    raise ResearchLibraryError("research run id was reused")
                variants = row.get("variants")
                if not isinstance(variants, list) or not 1 <= len(variants) <= self.max_variants_per_run:
                    raise ResearchLibraryError("opened run exceeds its frozen variant budget")
                hashes = [item.get("variant_sha256") for item in variants]
                if len(hashes) != len(set(hashes)) or any(not self._valid_sha(v) for v in hashes):
                    raise ResearchLibraryError("run has duplicate or malformed variant digests")
                if row.get("research_only") is not True or row.get("auto_promotion_allowed") is not False:
                    raise ResearchLibraryError("research run must be research-only and non-promotable")
                campaign_id = row.get("campaign_id", row.get("mechanism_id"))
                budget = row.get("campaign_budget", self.max_variants_per_run)
                familywise_alpha = row.get("familywise_alpha", 0.05)
                if (not isinstance(campaign_id, str) or not campaign_id.strip()
                        or type(budget) is not int or not 1 <= budget <= MAX_VARIANTS_PER_RUN
                        or isinstance(familywise_alpha, bool) or not isinstance(familywise_alpha, (int, float))
                        or not math.isfinite(familywise_alpha) or not 0 < familywise_alpha < 1):
                    raise ResearchLibraryError("research campaign identity, budget, or alpha is invalid")
                campaign = self._campaigns.setdefault(campaign_id, {
                    "candidate_count": 0, "budget": budget, "familywise_alpha": float(familywise_alpha),
                })
                if (campaign["budget"] != budget or campaign["familywise_alpha"] != float(familywise_alpha)
                        or campaign["candidate_count"] + len(variants) > budget):
                    raise ResearchLibraryError("run exceeds or changes its frozen campaign search budget")
                campaign["candidate_count"] += len(variants)
                if row.get("cumulative_candidate_count", campaign["candidate_count"]) != campaign["candidate_count"]:
                    raise ResearchLibraryError("campaign candidate-count ledger is inconsistent")
                self._runs[run_id] = {"expected": set(hashes), "variants": variants, "results": {}, "closed": False}
            elif row.get("t") == "RESEARCH_VARIANT_RESULT":
                state = self._active_run(run_id)
                digest, outcome = row.get("variant_sha256"), row.get("outcome")
                if digest not in state["expected"] or digest in state["results"] or outcome not in OUTCOMES:
                    raise ResearchLibraryError("variant result is unexpected, duplicated or invalid")
                if not self._valid_sha(row.get("details_sha256")) or canonical_sha256(row.get("details")) != row.get("details_sha256"):
                    raise ResearchLibraryError("variant result details digest mismatch")
                state["results"][digest] = outcome
            elif row.get("t") == "RESEARCH_RUN_CLOSED":
                state = self._active_run(run_id)
                if state["expected"] != set(state["results"]):
                    raise ResearchLibraryError("cannot close a research run with unlogged variants")
                state["closed"] = True
            else:
                raise ResearchLibraryError("unknown research journal event")

    @staticmethod
    def _valid_sha(value: Any) -> bool:
        return isinstance(value, str) and len(value) == 64 and all(char in _SHA for char in value)

    def _active_run(self, run_id: str) -> dict:
        state = self._runs.get(run_id)
        if state is None or state["closed"]:
            raise ResearchLibraryError("research run is unknown or already closed")
        return state

    def open_run(self, *, mechanism_id: str, mechanism_source_sha256: str, variants: list[dict],
                 seed: int, run_id: str | None = None, at_utc: datetime | None = None,
                 campaign_id: str | None = None, campaign_budget: int | None = None,
                 familywise_alpha: float = 0.05) -> str:
        if not mechanism_id.strip() or not self._valid_sha(mechanism_source_sha256):
            raise ResearchLibraryError("mechanism id and source digest are required")
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise ResearchLibraryError("integer research seed is required")
        if not 1 <= len(variants) <= self.max_variants_per_run:
            raise ResearchLibraryError(f"run must declare 1..{self.max_variants_per_run} variants")
        if at_utc is not None and (at_utc.tzinfo is None or at_utc.utcoffset() is None):
            raise ResearchLibraryError("run timestamp must be timezone-aware")
        campaign_key = campaign_id or mechanism_id
        budget = self.max_variants_per_run if campaign_budget is None else campaign_budget
        if (not isinstance(campaign_key, str) or not campaign_key.strip()
                or type(budget) is not int or not 1 <= budget <= MAX_VARIANTS_PER_RUN
                or isinstance(familywise_alpha, bool) or not isinstance(familywise_alpha, (int, float))
                or not math.isfinite(familywise_alpha) or not 0 < familywise_alpha < 1):
            raise ResearchLibraryError("campaign ID, fixed budget, and familywise alpha are required")
        campaign = self._campaigns.get(campaign_key)
        if campaign is not None and (campaign["budget"] != budget
                                     or campaign["familywise_alpha"] != float(familywise_alpha)):
            raise ResearchLibraryError("campaign budget and familywise alpha are immutable")
        prior_count = campaign["candidate_count"] if campaign else 0
        cumulative_count = prior_count + len(variants)
        if cumulative_count > budget:
            raise ResearchLibraryError("campaign candidate budget is exhausted")
        frozen = []
        for variant in variants:
            if not isinstance(variant, dict) or not isinstance(variant.get("variant_id"), str) \
                    or not variant["variant_id"].strip() or not isinstance(variant.get("parameters"), dict):
                raise ResearchLibraryError("each variant needs a stable id and parameter object")
            item = json_safe(variant)
            item["variant_sha256"] = canonical_sha256({"mechanism_id": mechanism_id,
                                                        "source_sha256": mechanism_source_sha256,
                                                        "variant": item})
            frozen.append(item)
        if len({item["variant_id"] for item in frozen}) != len(frozen):
            raise ResearchLibraryError("variant ids must be unique within a run")
        rid = run_id or str(uuid4())
        state = {"expected": {item["variant_sha256"] for item in frozen}, "variants": frozen,
                 "results": {}, "closed": False}
        if rid in self._runs:
            raise ResearchLibraryError("research run id was reused")
        self.journal.append({"t": "RESEARCH_RUN_OPENED", "run_id": rid, "mechanism_id": mechanism_id,
                             "mechanism_source_sha256": mechanism_source_sha256, "seed": seed,
                             "campaign_id": campaign_key, "campaign_budget": budget,
                             "familywise_alpha": float(familywise_alpha),
                             "cumulative_candidate_count": cumulative_count,
                             "variants": frozen, "variant_count": len(frozen),
                             "research_only": True, "auto_promotion_allowed": False,
                             "at_utc": (at_utc or datetime.now(timezone.utc)).isoformat()})
        self._runs[rid] = state
        self._campaigns[campaign_key] = {"candidate_count": cumulative_count, "budget": budget,
                                         "familywise_alpha": float(familywise_alpha)}
        return rid

    def record_result(self, run_id: str, variant_sha256: str, *, outcome: str, details: Any) -> None:
        state = self._active_run(run_id)
        if variant_sha256 not in state["expected"] or variant_sha256 in state["results"]:
            raise ResearchLibraryError("variant digest is not pending in this run")
        if outcome not in OUTCOMES:
            raise ResearchLibraryError(f"outcome must be one of {sorted(OUTCOMES)}")
        safe = json_safe(details)
        self.journal.append({"t": "RESEARCH_VARIANT_RESULT", "run_id": run_id,
                             "variant_sha256": variant_sha256, "outcome": outcome,
                             "details": safe, "details_sha256": canonical_sha256(safe),
                             "research_only": True})
        state["results"][variant_sha256] = outcome

    def abort_pending(self, run_id: str, *, reason: str) -> None:
        state = self._active_run(run_id)
        for digest in sorted(state["expected"] - set(state["results"])):
            self.record_result(run_id, digest, outcome="ABORTED", details={"reason": reason})

    def recover_incomplete_runs(self, *, reason: str = "process_restarted_before_completion") -> list[str]:
        """Close interrupted runs with explicit outcomes before a new single-writer run starts.

        This is intentionally an explicit operation: constructing a journal to inspect it does not
        mutate evidence. Callers must ensure no other process is actively writing this journal.
        """
        recovered = []
        for run_id, state in list(self._runs.items()):
            if state["closed"]:
                continue
            self.abort_pending(run_id, reason=reason)
            self.close_run(run_id)
            recovered.append(run_id)
        return recovered

    def close_run(self, run_id: str) -> None:
        state = self._active_run(run_id)
        if state["expected"] != set(state["results"]):
            missing = len(state["expected"] - set(state["results"]))
            raise ResearchLibraryError(f"cannot close with {missing} unlogged variants")
        self.journal.append({"t": "RESEARCH_RUN_CLOSED", "run_id": run_id,
                             "variant_count": len(state["expected"]), "research_only": True,
                             "auto_promotion_allowed": False})
        state["closed"] = True

    def run_state(self, run_id: str) -> dict:
        state = self._runs[run_id]
        return {"variant_count": len(state["expected"]), "recorded_count": len(state["results"]),
                "outcomes": dict(state["results"]), "closed": state["closed"]}

    def variant_digests(self, run_id: str) -> dict[str, str]:
        """Return the immutable id-to-digest map needed by an evaluator to record results."""
        state = self._runs[run_id]
        return {item["variant_id"]: item["variant_sha256"] for item in state["variants"]}

    def campaign_state(self, campaign_id: str) -> dict[str, float | int | str]:
        """Return the cumulative family denominator and its frozen Bonferroni alpha threshold."""
        state = self._campaigns[campaign_id]
        count = state["candidate_count"]
        return {"campaign_id": campaign_id, "candidate_count": count,
                "candidate_budget": state["budget"], "familywise_alpha": state["familywise_alpha"],
                "per_candidate_alpha": state["familywise_alpha"] / count,
                "remaining_budget": state["budget"] - count}
