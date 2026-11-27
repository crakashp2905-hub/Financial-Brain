"""Frozen abstention-policy evaluation with missed-winner accounting (FE-0084)."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Callable


class AbstentionError(ValueError):
    """Invalid frozen policy or incomplete abstention evaluation."""


class ExecutionDisposition(StrEnum):
    NO_ORDER = "NO_ORDER"
    REJECTED = "REJECTED"
    DELAYED = "DELAYED"
    NO_FILL = "NO_FILL"
    PARTIAL_FILL = "PARTIAL_FILL"
    FILLED = "FILLED"
    UNKNOWN = "UNKNOWN"


NO_EXECUTION = frozenset({ExecutionDisposition.NO_ORDER, ExecutionDisposition.REJECTED,
                          ExecutionDisposition.DELAYED, ExecutionDisposition.NO_FILL})


@dataclass(frozen=True)
class FrozenAbstentionPolicy:
    policy_id: str
    strategy_version_hash: str
    parameters_hash: str
    frozen_at: datetime
    first_eligible_session: date

    def __post_init__(self) -> None:
        if not all(x.strip() for x in (self.policy_id, self.strategy_version_hash, self.parameters_hash)):
            raise AbstentionError("policy identity and hashes are required")
        if self.frozen_at.tzinfo is None or self.frozen_at.utcoffset() is None:
            raise AbstentionError("policy freeze timestamp must be timezone aware")
        if self.frozen_at.date() >= self.first_eligible_session:
            raise AbstentionError("policy must be frozen before the cohort's first eligible session")

    def digest(self) -> str:
        body = {"policy_id": self.policy_id, "strategy_version_hash": self.strategy_version_hash,
                "parameters_hash": self.parameters_hash,
                "frozen_at": self.frozen_at.astimezone(timezone.utc).isoformat(),
                "first_eligible_session": self.first_eligible_session.isoformat()}
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class FrozenOpportunityManifest:
    """Exact opportunity denominator frozen before the first eligible cohort session."""

    opportunity_ids: tuple[str, ...]
    source_sha256: str
    frozen_at: datetime
    policy_sha256: str

    def __post_init__(self) -> None:
        if not self.opportunity_ids or any(not isinstance(x, str) or not x.strip()
                                           for x in self.opportunity_ids):
            raise AbstentionError("frozen opportunity manifest must be explicit and nonempty")
        if tuple(sorted(set(self.opportunity_ids))) != self.opportunity_ids:
            raise AbstentionError("manifest opportunity IDs must be unique and sorted")
        if len(self.source_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.source_sha256):
            raise AbstentionError("manifest source identity must be lowercase SHA-256")
        if len(self.policy_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.policy_sha256):
            raise AbstentionError("manifest policy identity must be lowercase SHA-256")
        if self.frozen_at.tzinfo is None or self.frozen_at.utcoffset() is None:
            raise AbstentionError("opportunity manifest freeze time must be timezone aware")

    def identity(self) -> dict[str, object]:
        return {"opportunity_ids": list(self.opportunity_ids), "source_sha256": self.source_sha256,
                "frozen_at": self.frozen_at.astimezone(timezone.utc).isoformat(),
                "policy_sha256": self.policy_sha256}

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.identity(), sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode("utf-8")).hexdigest()


class OpportunityManifestLedger:
    """Locally timestamped, append-only freeze receipts for exact opportunity universes."""

    def __init__(self, path: Path | str, *, clock: Callable[[], datetime] | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("""CREATE TABLE IF NOT EXISTS opportunity_manifest_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            manifest_sha256 TEXT NOT NULL UNIQUE,
            body_json TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            event_hash TEXT NOT NULL UNIQUE)""")

    def close(self) -> None:
        self._con.close()

    def _now(self) -> datetime:
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise AbstentionError("manifest ledger clock must return a timezone-aware datetime")
        return now.astimezone(timezone.utc)

    def freeze(self, policy: FrozenAbstentionPolicy, *, opportunity_ids: tuple[str, ...],
               source_sha256: str) -> FrozenOpportunityManifest:
        frozen_at = self._now()
        if frozen_at < policy.frozen_at.astimezone(timezone.utc):
            raise AbstentionError("opportunity manifest cannot precede its frozen policy")
        if frozen_at.date() >= policy.first_eligible_session:
            raise AbstentionError("opportunity manifest must be frozen before the first eligible session")
        manifest = FrozenOpportunityManifest(opportunity_ids, source_sha256, frozen_at, policy.digest())
        body = json.dumps(manifest.identity(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        manifest_hash = manifest.digest()
        self._con.execute("BEGIN IMMEDIATE")
        try:
            tail = self._con.execute(
                "SELECT event_hash FROM opportunity_manifest_events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            previous = tail[0] if tail else "0" * 64
            event_hash = hashlib.sha256((previous + body).encode("utf-8")).hexdigest()
            self._con.execute("INSERT INTO opportunity_manifest_events "
                              "(manifest_sha256,body_json,previous_hash,event_hash) VALUES (?,?,?,?)",
                              (manifest_hash, body, previous, event_hash))
            self._con.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            self._con.execute("ROLLBACK")
            raise AbstentionError("opportunity manifest was already frozen") from exc
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        return manifest

    def verify(self, policy: FrozenAbstentionPolicy, manifest: FrozenOpportunityManifest) -> None:
        if manifest.policy_sha256 != policy.digest():
            raise AbstentionError("opportunity manifest belongs to a different frozen policy")
        self.audit()
        body = json.dumps(manifest.identity(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        row = self._con.execute(
            "SELECT body_json FROM opportunity_manifest_events WHERE manifest_sha256=?",
            (manifest.digest(),),
        ).fetchone()
        if row is None or row[0] != body:
            raise AbstentionError("opportunity manifest is not present in the durable freeze ledger")

    def audit(self) -> dict[str, object]:
        rows = self._con.execute(
            "SELECT manifest_sha256,body_json,previous_hash,event_hash "
            "FROM opportunity_manifest_events ORDER BY sequence"
        ).fetchall()
        previous = "0" * 64
        for manifest_hash, body, prior, event_hash in rows:
            record = json.loads(body)
            observed_hash = hashlib.sha256(json.dumps(
                record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            ).hexdigest()
            if (prior != previous or hashlib.sha256((prior + body).encode("utf-8")).hexdigest() != event_hash
                    or observed_hash != manifest_hash):
                raise AbstentionError("opportunity manifest ledger hash chain is corrupt")
            previous = event_hash
        return {"manifests": len(rows), "head_sha256": previous, "integrity": "VALID"}


@dataclass(frozen=True)
class AbstentionOutcome:
    opportunity_id: str
    session: date
    policy_id: str
    policy_hash: str
    action: str
    outcome_status: str
    policy_realized_net_return: float | None
    counterfactual_net_return: float | None
    abstain_reason: str | None = None
    execution_disposition: ExecutionDisposition = ExecutionDisposition.UNKNOWN
    fill_fraction: float | None = None

    def __post_init__(self) -> None:
        if not all(x.strip() for x in (self.opportunity_id, self.policy_id, self.policy_hash)):
            raise AbstentionError("opportunity and policy lineage are required")
        if self.action not in {"TRADE", "ABSTAIN"} or self.outcome_status not in {"RESOLVED", "UNRESOLVED"}:
            raise AbstentionError("invalid policy action or outcome status")
        if not isinstance(self.execution_disposition, ExecutionDisposition):
            raise AbstentionError("execution disposition must use the closed status enum")
        if self.fill_fraction is not None and (
            not math.isfinite(self.fill_fraction) or not 0 <= self.fill_fraction <= 1
        ):
            raise AbstentionError("fill fraction must be finite and in [0, 1]")
        if self.execution_disposition is ExecutionDisposition.UNKNOWN:
            if self.fill_fraction is not None:
                raise AbstentionError("unknown execution cannot claim a fill fraction")
            if self.outcome_status != "UNRESOLVED":
                raise AbstentionError("unknown execution must remain unresolved")
        elif self.execution_disposition is ExecutionDisposition.FILLED:
            if self.fill_fraction != 1:
                raise AbstentionError("filled execution requires a 100% fill fraction")
        elif self.execution_disposition is ExecutionDisposition.PARTIAL_FILL:
            if self.fill_fraction is None or not 0 < self.fill_fraction < 1:
                raise AbstentionError("partial fill requires a fill fraction strictly between 0 and 1")
        elif self.fill_fraction not in (None, 0):
            raise AbstentionError("no-fill execution states cannot claim filled quantity")
        if self.action == "ABSTAIN" and self.execution_disposition not in {
            ExecutionDisposition.NO_ORDER, ExecutionDisposition.REJECTED,
        }:
            raise AbstentionError("abstention must have no order or a recorded rejection")
        if self.action == "TRADE" and self.execution_disposition is ExecutionDisposition.NO_ORDER:
            raise AbstentionError("trade decision must preserve its order attempt disposition")
        for name, value in (("policy_realized_net_return", self.policy_realized_net_return),
                            ("counterfactual_net_return", self.counterfactual_net_return)):
            if value is not None and not math.isfinite(float(value)):
                raise AbstentionError(f"{name} must be finite when supplied")
        if self.outcome_status == "RESOLVED":
            if self.policy_realized_net_return is None or self.counterfactual_net_return is None:
                raise AbstentionError("resolved outcomes require realized and counterfactual net returns")
            if self.action == "ABSTAIN" and self.policy_realized_net_return != 0:
                raise AbstentionError("abstention cannot realize a trade return")
        elif self.policy_realized_net_return is not None or self.counterfactual_net_return is not None:
            raise AbstentionError("unresolved outcomes cannot carry invented returns")
        if self.action == "ABSTAIN" and not (self.abstain_reason and self.abstain_reason.strip()):
            raise AbstentionError("abstention reason is required")


@dataclass(frozen=True)
class AbstentionReport:
    policy_id: str
    policy_hash: str
    opportunity_manifest_hash: str
    total_opportunities: int
    traded: int
    abstained: int
    resolved: int
    unresolved: int
    realized_policy_net_return_sum: float
    resolved_counterfactual_net_return_sum: float
    counterfactual_minus_policy_net_return_sum: float
    unexecuted_counterfactual_net_return_sum: float
    missed_winners: int
    abstention_avoided_losses: int
    unresolved_abstentions: int
    execution_counts: tuple[tuple[str, int], ...]
    missed_execution_winners: int
    execution_avoided_losses: int
    report_hash: str


def evaluate_abstentions(policy: FrozenAbstentionPolicy, manifest: FrozenOpportunityManifest,
                         outcomes: tuple[AbstentionOutcome, ...], *,
                         manifest_ledger: OpportunityManifestLedger) -> AbstentionReport:
    """Evaluate every declared opportunity; unresolved outcomes stay in the denominator."""
    if not isinstance(manifest, FrozenOpportunityManifest):
        raise AbstentionError("a frozen opportunity manifest is required")
    if not isinstance(manifest_ledger, OpportunityManifestLedger):
        raise AbstentionError("durable opportunity manifest ledger is required")
    manifest_ledger.verify(policy, manifest)
    if manifest.frozen_at < policy.frozen_at.astimezone(timezone.utc):
        raise AbstentionError("opportunity universe cannot be frozen before its policy")
    if manifest.frozen_at.date() >= policy.first_eligible_session:
        raise AbstentionError("opportunity universe must be frozen before the first eligible session")
    if not outcomes:
        raise AbstentionError("all expected opportunities need a decision/outcome row")
    policy_hash = policy.digest()
    for item in outcomes:
        if item.policy_id != policy.policy_id or item.policy_hash != policy_hash:
            raise AbstentionError("evaluation policy lineage differs from the frozen policy")
        if item.session < policy.first_eligible_session:
            raise AbstentionError("outcome predates the policy's first eligible session")
    by_id = {item.opportunity_id: item for item in outcomes}
    if len(by_id) != len(outcomes) or set(by_id) != set(manifest.opportunity_ids):
        raise AbstentionError("outcome IDs must exactly match the pre-frozen opportunity manifest")
    ordered = [by_id[key] for key in sorted(by_id)]
    traded = sum(item.action == "TRADE" for item in ordered)
    abstained = len(ordered) - traded
    resolved = [item for item in ordered if item.outcome_status == "RESOLVED"]
    unresolved = len(ordered) - len(resolved)
    realized = sum(float(item.policy_realized_net_return or 0.0) for item in resolved)
    counterfactual = sum(float(item.counterfactual_net_return or 0.0) for item in resolved)
    counterfactual_minus_policy = sum(
        float(item.counterfactual_net_return or 0.0) - float(item.policy_realized_net_return or 0.0)
        for item in resolved
    )
    unexecuted_counterfactual = sum(
        float(item.counterfactual_net_return or 0.0) for item in resolved
        if item.execution_disposition in NO_EXECUTION
    )
    missed = sum(item.action == "ABSTAIN" and float(item.counterfactual_net_return or 0.0) > 0
                 for item in resolved)
    avoided = sum(item.action == "ABSTAIN" and float(item.counterfactual_net_return or 0.0) < 0
                  for item in resolved)
    unresolved_abstentions = sum(item.action == "ABSTAIN" and item.outcome_status == "UNRESOLVED"
                                 for item in ordered)
    execution_counts = tuple((state.value, sum(item.execution_disposition is state for item in ordered))
                             for state in ExecutionDisposition)
    missed_execution_winners = sum(
        item.outcome_status == "RESOLVED" and item.execution_disposition in NO_EXECUTION
        and float(item.counterfactual_net_return or 0.0) > 0 for item in ordered
    )
    execution_avoided_losses = sum(
        item.outcome_status == "RESOLVED" and item.execution_disposition in NO_EXECUTION
        and float(item.counterfactual_net_return or 0.0) < 0 for item in ordered
    )
    manifest_hash = manifest.digest()
    fields = {"policy_id": policy.policy_id, "policy_hash": policy_hash,
              "opportunity_manifest_hash": manifest_hash,
              "total_opportunities": len(ordered), "traded": traded, "abstained": abstained,
              "resolved": len(resolved), "unresolved": unresolved,
              "realized_policy_net_return_sum": realized,
              "resolved_counterfactual_net_return_sum": counterfactual,
              "counterfactual_minus_policy_net_return_sum": counterfactual_minus_policy,
              "unexecuted_counterfactual_net_return_sum": unexecuted_counterfactual,
              "missed_winners": missed,
              "abstention_avoided_losses": avoided, "unresolved_abstentions": unresolved_abstentions,
              "execution_counts": execution_counts,
              "missed_execution_winners": missed_execution_winners,
              "execution_avoided_losses": execution_avoided_losses}
    digest = hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return AbstentionReport(policy.policy_id, policy_hash, manifest_hash, len(ordered), traded, abstained,
                            len(resolved), unresolved, realized, counterfactual,
                            counterfactual_minus_policy, unexecuted_counterfactual, missed, avoided,
                            unresolved_abstentions, execution_counts, missed_execution_winners,
                            execution_avoided_losses, digest)


__all__ = ["AbstentionError", "AbstentionOutcome", "AbstentionReport", "ExecutionDisposition",
           "FrozenAbstentionPolicy", "FrozenOpportunityManifest", "OpportunityManifestLedger",
           "evaluate_abstentions"]
