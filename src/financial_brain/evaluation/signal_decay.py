"""Training-window signal-information decay and turnover burden by family."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime
import hashlib
import json
import math

from .half_life import half_life


class SignalDecayError(ValueError):
    """Malformed, unregistered, or protected-window decay evidence."""


@dataclass(frozen=True)
class SignalDecayPlan:
    plan_id: str
    protocol_sha256: str
    development_data_sha256: str
    development_start: str
    development_end: str
    protected_final_start: str
    protected_final_end: str
    frozen_at_utc: str
    families: tuple[str, ...]
    horizons: tuple[int, ...]

    def __post_init__(self) -> None:
        for name in ("plan_id",):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise SignalDecayError(f"{name} is required")
        for name in ("protocol_sha256", "development_data_sha256"):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise SignalDecayError(f"{name} must be a lowercase SHA-256 digest")
        try:
            start, end, final_start, final_end = map(date.fromisoformat, (
                self.development_start, self.development_end,
                self.protected_final_start, self.protected_final_end,
            ))
            frozen = datetime.fromisoformat(self.frozen_at_utc.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise SignalDecayError("plan dates or freeze timestamp are invalid") from exc
        if (start > end or end >= final_start or final_start > final_end
                or frozen.tzinfo is None or frozen.utcoffset() is None or frozen.date() > start):
            raise SignalDecayError("development/final windows or pre-observation freeze are invalid")
        if not self.families or any(not isinstance(item, str) or not item.strip() for item in self.families):
            raise SignalDecayError("families must be nonempty and unique")
        if len(set(self.families)) != len(self.families):
            raise SignalDecayError("families must be nonempty and unique")
        if (len(self.horizons) < 3
                or any(isinstance(h, bool) or not isinstance(h, int) or h <= 0 for h in self.horizons)
                or len(set(self.horizons)) != len(self.horizons)):
            raise SignalDecayError("at least three unique positive integer horizons are required")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()


def _finite(row: Mapping[str, object], field: str) -> float:
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SignalDecayError(f"{field} must be finite")
    return float(value)


def analyze_signal_decay(*, plan: SignalDecayPlan, observations: Iterable[Mapping[str, object]]) -> dict:
    """Analyze all registered family/horizon cells, retaining every training row.

    The measured peak is a research diagnostic. No existing strategy horizon is
    altered; use requires registration as a new strategy version.
    """
    rows = []
    cells: dict[tuple[str, int], list[dict]] = {
        (family, horizon): [] for family in plan.families for horizon in plan.horizons
    }
    identities: dict[str, dict[int, set[tuple[str, str]]]] = {
        family: {horizon: set() for horizon in plan.horizons} for family in plan.families
    }
    seen = set()
    for row in observations:
        if not isinstance(row, Mapping) or row.get("split") != "DEVELOPMENT":
            raise SignalDecayError("signal-decay analysis reads DEVELOPMENT rows only")
        try:
            family = row["family"]
            horizon = row["horizon_sessions"]
            day = date.fromisoformat(row["session_date"]).isoformat()
            decision_id = row["decision_sha256"]
        except (KeyError, TypeError, ValueError) as exc:
            raise SignalDecayError("row requires family, horizon, session date, and decision hash") from exc
        if family not in plan.families or horizon not in plan.horizons:
            raise SignalDecayError("family or horizon is outside the frozen plan")
        if not (plan.development_start <= day <= plan.development_end) or day >= plan.protected_final_start:
            raise SignalDecayError("observation touches data outside the development window")
        if (not isinstance(decision_id, str) or len(decision_id) != 64
                or any(c not in "0123456789abcdef" for c in decision_id)):
            raise SignalDecayError("decision_sha256 must be lowercase SHA-256")
        ic = _finite(row, "information_coefficient")
        alpha = _finite(row, "net_alpha_fraction")
        turnover = _finite(row, "turnover_fraction")
        if not -1 <= ic <= 1 or alpha <= -1 or turnover < 0:
            raise SignalDecayError("net alpha must exceed -100% and turnover must be nonnegative")
        key = (family, horizon, day, decision_id)
        if key in seen:
            raise SignalDecayError("duplicate family/horizon/session/decision row")
        seen.add(key)
        normalized = {"split": "DEVELOPMENT", "family": family, "horizon_sessions": horizon,
                      "session_date": day, "decision_sha256": decision_id,
                      "information_coefficient": ic, "net_alpha_fraction": alpha,
                      "turnover_fraction": turnover}
        rows.append(normalized)
        cells[(family, horizon)].append(normalized)
        identities[family][horizon].add((day, decision_id))
    if not rows:
        raise SignalDecayError("development observations are required")
    reports = {}
    for family in plan.families:
        reference: set[tuple[str, str]] | None = None
        mean_ic, horizon_results = {}, {}
        for horizon in sorted(plan.horizons):
            group = cells[(family, horizon)]
            if not group:
                raise SignalDecayError("every family/horizon cell needs observations")
            ids = identities[family][horizon]
            if reference is None:
                reference = ids
            elif ids != reference:
                raise SignalDecayError("all horizons within a family must use the same sessions/decisions")
            mean_ic[horizon] = sum(row["information_coefficient"] for row in group) / len(group)
            net_alpha = sum(row["net_alpha_fraction"] for row in group) / len(group)
            turnover = sum(row["turnover_fraction"] for row in group) / len(group)
            ratio = turnover / abs(net_alpha) if abs(net_alpha) > 1e-12 else None
            horizon_results[horizon] = {
                "n_sessions": len(group), "mean_information_coefficient": mean_ic[horizon],
                "mean_daily_net_alpha_fraction": net_alpha,
                "mean_turnover_fraction": turnover,
                "turnover_to_absolute_net_alpha": ratio,
                "turnover_alpha_status": "DEFINED" if ratio is not None else "UNDEFINED_NEAR_ZERO_ALPHA",
            }
        decay = half_life(mean_ic)
        reports[family] = {
            "time_to_peak_information_horizon": decay["peak_h"],
            "peak_information_coefficient": decay["peak_ic"],
            "signal_half_life_sessions": decay["half_life"],
            "still_rising_at_longest_horizon": decay.get("still_rising", False),
            "by_horizon": horizon_results,
            "horizon_action": "NO_CHANGE; any adoption requires a newly registered strategy version",
        }
    rows.sort(key=lambda row: (row["family"], row["horizon_sessions"],
                               row["session_date"], row["decision_sha256"]))
    return {
        "plan_id": plan.plan_id, "plan_sha256": plan.sha256,
        "development_data_sha256": plan.development_data_sha256,
        "families": reports, "observation_count": len(rows), "observations": rows,
        "outcome_sha256": hashlib.sha256(json.dumps(rows, sort_keys=True,
                                                        separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
        "claim_scope": "Development diagnostic only; no existing horizon is changed and no profitability claim is made.",
    }
