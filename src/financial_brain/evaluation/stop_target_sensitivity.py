"""Development-only stop/target sensitivity with adverse OHLC collision resolution."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime
import hashlib
import json
import math

from financial_brain.kernel.fills import OhlcBar, resolve_stop_target


class StopTargetSensitivityError(ValueError):
    """Invalid stop/target experiment or bar-path evidence."""


@dataclass(frozen=True)
class StopTargetPlan:
    plan_id: str
    strategy_sha256: str
    development_data_sha256: str
    development_start: str
    development_end: str
    protected_final_start: str
    protected_final_end: str
    frozen_at_utc: str
    stop_fractions: tuple[float, ...]
    target_fractions: tuple[float, ...]
    max_combinations: int

    def __post_init__(self) -> None:
        for name in ("plan_id",):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise StopTargetSensitivityError("plan_id is required")
        for name in ("strategy_sha256", "development_data_sha256"):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise StopTargetSensitivityError(f"{name} must be lowercase SHA-256")
        try:
            start, end, final_start, final_end = map(date.fromisoformat, (
                self.development_start, self.development_end,
                self.protected_final_start, self.protected_final_end,
            ))
            frozen = datetime.fromisoformat(self.frozen_at_utc.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise StopTargetSensitivityError("plan dates or freeze timestamp are invalid") from exc
        if (start > end or end >= final_start or final_start > final_end
                or frozen.tzinfo is None or frozen.utcoffset() is None or frozen.date() > start):
            raise StopTargetSensitivityError("development/final windows or pre-search freeze are invalid")
        for name, values in (("stop", self.stop_fractions), ("target", self.target_fractions)):
            if (not values or any(isinstance(x, bool) or not isinstance(x, (int, float))
                                  or not math.isfinite(x) or not 0 < x < 1 for x in values)
                    or len(set(values)) != len(values)):
                raise StopTargetSensitivityError(f"{name} fractions must be unique and between 0 and 1")
        count = len(self.stop_fractions) * len(self.target_fractions)
        if isinstance(self.max_combinations, bool) or self.max_combinations < count:
            raise StopTargetSensitivityError("search budget is smaller than the predeclared grid")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()


def _finite(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise StopTargetSensitivityError(f"{label} must be finite")
    return float(value)


def _validate_paths(rows: Iterable[Mapping[str, object]], plan: StopTargetPlan) -> list[dict]:
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, Mapping) or row.get("split") != "DEVELOPMENT":
            raise StopTargetSensitivityError("sensitivity study accepts DEVELOPMENT trades only")
        try:
            trade_id = row["trade_id"]
            trade_day = date.fromisoformat(row["session_date"]).isoformat()
            decision_sha = row["decision_sha256"]
            side = row["side"]
            bars = row["bars"]
        except (KeyError, TypeError, ValueError) as exc:
            raise StopTargetSensitivityError("trade requires id, date, decision hash, side and bars") from exc
        if not isinstance(trade_id, str) or not trade_id.strip() or trade_id in seen:
            raise StopTargetSensitivityError("trade_id must be unique and nonempty")
        seen.add(trade_id)
        if not plan.development_start <= trade_day <= plan.development_end:
            raise StopTargetSensitivityError("trade date is outside the development window")
        if not isinstance(decision_sha, str) or len(decision_sha) != 64 or any(c not in "0123456789abcdef" for c in decision_sha):
            raise StopTargetSensitivityError("decision_sha256 must be lowercase SHA-256")
        if side not in ("BUY", "SELL"):
            raise StopTargetSensitivityError("side must be BUY or SELL")
        entry = _finite(row.get("entry_price"), "entry_price")
        notional = _finite(row.get("notional_inr"), "notional_inr")
        costs = _finite(row.get("round_trip_costs_inr"), "round_trip_costs_inr")
        if entry <= 0 or notional <= 0 or costs < 0:
            raise StopTargetSensitivityError("entry and notional must be positive; costs nonnegative")
        if not isinstance(bars, (list, tuple)) or not bars:
            raise StopTargetSensitivityError("one or more post-entry OHLC bars are required")
        normalized_bars = []
        for bar in bars:
            try:
                bar_day = date.fromisoformat(bar["session_date"]).isoformat()
            except (KeyError, TypeError, ValueError) as exc:
                raise StopTargetSensitivityError("each OHLC bar requires session_date") from exc
            if not plan.development_start <= bar_day <= plan.development_end or bar_day >= plan.protected_final_start:
                raise StopTargetSensitivityError("bar path touches data outside the development window")
            values = {name: _finite(bar.get(name), f"bar {name}")
                      for name in ("open", "high", "low", "close", "volume")}
            if (values["open"] <= 0 or values["close"] <= 0 or values["low"] <= 0
                    or values["high"] < values["low"] or values["volume"] < 0
                    or not values["low"] <= values["open"] <= values["high"]
                    or not values["low"] <= values["close"] <= values["high"]):
                raise StopTargetSensitivityError("OHLC bar is internally inconsistent")
            normalized_bars.append({"session_date": bar_day, **values})
        result.append({"trade_id": trade_id, "session_date": trade_day, "decision_sha256": decision_sha,
                       "side": side, "entry_price": entry, "notional_inr": notional,
                       "round_trip_costs_inr": costs, "bars": normalized_bars})
    if not result:
        raise StopTargetSensitivityError("development trades are required")
    return sorted(result, key=lambda row: (row["session_date"], row["trade_id"]))


def evaluate_stop_target_sensitivity(*, plan: StopTargetPlan,
                                     trades: Iterable[Mapping[str, object]]) -> dict:
    """Evaluate every frozen stop/target cell; same-bar hits use stop-first adverse fills."""
    paths = _validate_paths(trades, plan)
    evaluations = []
    all_outcomes = []
    for stop_fraction in plan.stop_fractions:
        for target_fraction in plan.target_fractions:
            cell = []
            for trade in paths:
                entry, side = trade["entry_price"], trade["side"]
                stop = entry * (1 - stop_fraction) if side == "BUY" else entry * (1 + stop_fraction)
                target = entry * (1 + target_fraction) if side == "BUY" else entry * (1 - target_fraction)
                exit_price, exit_reason = trade["bars"][-1]["close"], "TIME"
                for bar in trade["bars"]:
                    outcome, price = resolve_stop_target(OhlcBar(**{k: bar[k] for k in
                        ("open", "high", "low", "close", "volume")}), side, stop, target)
                    if outcome != "NONE":
                        exit_price, exit_reason = price, outcome
                        break
                sign = 1 if side == "BUY" else -1
                gross = sign * (exit_price - entry) / entry
                cost_fraction = trade["round_trip_costs_inr"] / trade["notional_inr"]
                net = gross - cost_fraction
                outcome_row = {"trade_id": trade["trade_id"], "session_date": trade["session_date"],
                               "stop_fraction": stop_fraction, "target_fraction": target_fraction,
                               "exit_reason": exit_reason, "exit_price": exit_price,
                               "gross_return_fraction": gross, "cost_fraction": cost_fraction,
                               "net_return_fraction": net}
                cell.append(outcome_row)
                all_outcomes.append(outcome_row)
            net = [row["net_return_fraction"] for row in cell]
            evaluations.append({"stop_fraction": stop_fraction, "target_fraction": target_fraction,
                                "trade_count": len(cell), "mean_net_return_fraction": sum(net) / len(net),
                                "negative_trades": sum(value < 0 for value in net),
                                "same_bar_adverse_stops": sum(row["exit_reason"] == "STOP_AMBIGUOUS_ADVERSE" for row in cell),
                                "disposition": "DEVELOPMENT_DIAGNOSTIC_ONLY"})
    all_outcomes.sort(key=lambda row: (row["session_date"], row["trade_id"],
                                       row["stop_fraction"], row["target_fraction"]))
    return {"plan_sha256": plan.sha256, "development_data_sha256": plan.development_data_sha256,
            "trades": len(paths), "combination_count": len(evaluations),
            "evaluations": evaluations, "outcomes": all_outcomes,
            "outcome_sha256": hashlib.sha256(json.dumps(all_outcomes, sort_keys=True,
                                                            separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
            "bar_collision_policy": "Same-bar stop and target hits resolve to adverse stop; no favorable path ordering is assumed.",
            "claim_scope": "Training-window sensitivity only; no validation or profitability claim.",
            "protected_final_window": [plan.protected_final_start, plan.protected_final_end]}
