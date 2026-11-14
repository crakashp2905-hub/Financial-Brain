"""Same-session, size/liquidity/risk-matched opportunity controls (diagnostic only)."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math


class OpportunityControlError(ValueError):
    """Malformed treated or control opportunity input."""


@dataclass(frozen=True)
class MatchPolicy:
    policy_id: str
    max_size_relative_difference: float = 0.25
    max_liquidity_relative_difference: float = 0.25
    max_risk_relative_difference: float = 0.10

    def __post_init__(self):
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise OpportunityControlError("policy_id is required")
        for name in ("max_size_relative_difference", "max_liquidity_relative_difference",
                     "max_risk_relative_difference"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise OpportunityControlError(f"{name} must be finite and nonnegative")

    def sha256(self) -> str:
        payload = {"policy_id": self.policy_id,
                   "max_size_relative_difference": self.max_size_relative_difference,
                   "max_liquidity_relative_difference": self.max_liquidity_relative_difference,
                   "max_risk_relative_difference": self.max_risk_relative_difference}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()


_REQUIRED = ("session_date", "net_return_bp", "notional_inr", "liquidity_bp", "risk_budget_inr")


def _validate(rows: Sequence[Mapping[str, object]], identity: str, label: str) -> list[dict]:
    result = []
    seen = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise OpportunityControlError(f"{label} rows must be mappings")
        key = row.get(identity)
        if not isinstance(key, str) or not key.strip() or key in seen:
            raise OpportunityControlError(f"{label} {identity} must be unique and nonempty")
        seen.add(key)
        parsed = dict(row)
        for name in _REQUIRED:
            value = parsed.get(name)
            if name == "session_date":
                if not isinstance(value, str) or not value.strip():
                    raise OpportunityControlError(f"{label} session_date is required")
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise OpportunityControlError(f"{label} {name} must be finite")
            parsed[name] = float(value)
        if parsed["notional_inr"] <= 0 or parsed["risk_budget_inr"] <= 0 or parsed["liquidity_bp"] < 0:
            raise OpportunityControlError(f"{label} requires positive size/risk and nonnegative spread")
        result.append(parsed)
    return result


def _relative_difference(left: float, right: float) -> float:
    scale = max(abs(left), abs(right))
    return 0.0 if scale == 0 else abs(left - right) / scale


def compare_matched_controls(
    treated: Sequence[Mapping[str, object]],
    controls: Sequence[Mapping[str, object]],
    *,
    policy: MatchPolicy,
) -> dict:
    """Pair same-session opportunities without control reuse and keep every treated row.

    ``net_return_bp`` must already be net of modeled/realized trading costs. Controls
    can represent a matched no-trade/cash portfolio, but this function does not
    infer causal benefit from a filtered or dropped trade.
    """
    if not treated:
        raise OpportunityControlError("treated opportunity population is required")
    ts = _validate(treated, "trade_id", "treated")
    cs = _validate(controls, "control_id", "control")
    edges: dict[str, list[tuple[float, str]]] = {}
    by_id = {row["trade_id"]: row for row in ts}
    control_by_id = {row["control_id"]: row for row in cs}
    for t in ts:
        candidates = []
        for c in cs:
            if c["session_date"] != t["session_date"]:
                continue
            size = _relative_difference(t["notional_inr"], c["notional_inr"])
            liq = _relative_difference(t["liquidity_bp"], c["liquidity_bp"])
            risk = _relative_difference(t["risk_budget_inr"], c["risk_budget_inr"])
            if (size <= policy.max_size_relative_difference
                    and liq <= policy.max_liquidity_relative_difference
                    and risk <= policy.max_risk_relative_difference):
                candidates.append((size + liq + risk, c["control_id"]))
        edges[t["trade_id"]] = sorted(candidates, key=lambda pair: (pair[0], pair[1]))

    owner: dict[str, str] = {}

    def assign(trade_id: str, seen: set[str]) -> bool:
        for _, control_id in edges[trade_id]:
            if control_id in seen:
                continue
            seen.add(control_id)
            if control_id not in owner or assign(owner[control_id], seen):
                owner[control_id] = trade_id
                return True
        return False

    for trade_id in sorted(by_id):
        assign(trade_id, set())
    paired = {trade_id: control_id for control_id, trade_id in owner.items()}
    rows = []
    for t in sorted(ts, key=lambda row: (row["session_date"], row["trade_id"])):
        tid = t["trade_id"]
        cid = paired.get(tid)
        if cid is None:
            reason = ("NO_CONTEMPORANEOUS_CONTROL" if not any(c["session_date"] == t["session_date"] for c in cs)
                      else "NO_CONTROL_WITHIN_CALIPERS" if not edges[tid]
                      else "ELIGIBLE_CONTROLS_EXHAUSTED")
            rows.append({"trade_id": tid, "session_date": t["session_date"], "status": "UNMATCHED",
                         "reason": reason, "control_id": None, "net_difference_bp": None})
            continue
        c = control_by_id[cid]
        rows.append({"trade_id": tid, "session_date": t["session_date"], "status": "MATCHED",
                     "control_id": cid, "net_difference_bp": t["net_return_bp"] - c["net_return_bp"],
                     "treated_net_return_bp": t["net_return_bp"], "control_net_return_bp": c["net_return_bp"],
                     "size_relative_difference": _relative_difference(t["notional_inr"], c["notional_inr"]),
                     "liquidity_relative_difference": _relative_difference(t["liquidity_bp"], c["liquidity_bp"]),
                     "risk_relative_difference": _relative_difference(t["risk_budget_inr"], c["risk_budget_inr"])})
    differences = [row["net_difference_bp"] for row in rows if row["status"] == "MATCHED"]
    return {
        "policy_id": policy.policy_id,
        "policy_sha256": policy.sha256(),
        "treated_count": len(ts),
        "matched_count": len(differences),
        "unmatched_count": len(ts) - len(differences),
        "mean_matched_net_difference_bp": sum(differences) / len(differences) if differences else None,
        "pairs": rows,
        "causal_limitations": [
            "Matching is observational; unmeasured confounding and model misspecification remain.",
            "Matched differences do not establish that filtering or dropping losing trades improves performance.",
            "Unmatched treated opportunities remain in the denominator and are not assigned a control outcome.",
            "No inferential confidence interval or strategy promotion claim is produced.",
        ],
    }
