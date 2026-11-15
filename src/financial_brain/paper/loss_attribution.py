"""Reconcile a daily P&L bridge without silently assigning missing effects to zero.

Every component is a signed INR contribution to net P&L. Costs are therefore
negative values. Caller evidence must define forecast, selection, execution,
timing and sizing effects on one consistent counterfactual basis.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
import math


COMPONENTS = (
    "forecast_gross_inr",
    "selection_effect_inr",
    "spread_effect_inr",
    "impact_effect_inr",
    "charges_effect_inr",
    "timing_effect_inr",
    "risk_sizing_effect_inr",
)
REQUIRED = (*COMPONENTS, "realized_net_inr")


class LossAttributionError(ValueError):
    """Malformed P&L bridge input."""


def daily_loss_bridge(
    rows: Sequence[Mapping[str, object]], *, tolerance_inr: float = 0.01,
    scheduled_days: Sequence[str] | None = None,
    daily_realized_net_inr: Mapping[str, float | None] | None = None,
) -> dict:
    """Aggregate trade bridges and disclose missing components and residuals by day.

    A day is ``RECONCILED`` only when every required component exists for every
    trade and its signed effects sum to recorded net P&L within the declared
    tolerance. Missing component values are never interpreted as zero.
    """
    if not math.isfinite(tolerance_inr) or tolerance_inr < 0:
        raise LossAttributionError("tolerance_inr must be finite and nonnegative")
    if not rows:
        if scheduled_days is None or daily_realized_net_inr is None:
            raise LossAttributionError(
                "trade rows or a scheduled daily-ledger population are required"
            )

    if scheduled_days is None:
        if daily_realized_net_inr is not None:
            raise LossAttributionError("daily ledger totals require scheduled_days")
        days = None
    else:
        try:
            days = tuple(date.fromisoformat(value).isoformat() for value in scheduled_days)
        except (TypeError, ValueError) as exc:
            raise LossAttributionError("scheduled_days must contain ISO dates") from exc
        if not days or days != tuple(scheduled_days) or days != tuple(sorted(set(days))):
            raise LossAttributionError("scheduled_days must be nonempty, unique and ordered")
        if daily_realized_net_inr is None or set(daily_realized_net_inr) != set(days):
            raise LossAttributionError("daily ledger totals must cover every scheduled day")

    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise LossAttributionError("each trade bridge must be a mapping")
        day, trade_id = row.get("day"), row.get("trade_id")
        if not isinstance(day, str) or not day.strip():
            raise LossAttributionError("day must be a nonempty string")
        if not isinstance(trade_id, str) or not trade_id.strip():
            raise LossAttributionError("trade_id must be a nonempty string")
        identity = f"{day}\0{trade_id}"
        if identity in seen:
            raise LossAttributionError("duplicate trade_id within day")
        seen.add(identity)
        grouped[day].append(row)

    if days is not None and set(grouped) - set(days):
        raise LossAttributionError("trade rows contain an unscheduled day")

    reports = []
    for day in days if days is not None else sorted(grouped):
        day_rows = grouped.get(day, [])
        ledger_value = None if daily_realized_net_inr is None else daily_realized_net_inr[day]
        if ledger_value is not None and (
            isinstance(ledger_value, bool) or not isinstance(ledger_value, (int, float))
            or not math.isfinite(ledger_value)
        ):
            raise LossAttributionError("daily realized net must be a finite INR number or null")
        if not day_rows:
            day_missing: dict[str, list[str]]
            components: dict[str, float | None]
            if ledger_value is None:
                day_missing = {name: ["<MISSING_DAILY_LEDGER>"] for name in REQUIRED}
                components = {name: None for name in COMPONENTS}
                residual = None
                status = "INCOMPLETE"
            elif abs(float(ledger_value)) <= tolerance_inr:
                day_missing = {}
                components = {name: 0.0 for name in COMPONENTS}
                residual = 0.0
                status = "RECONCILED"
            else:
                day_missing = {name: ["<NO_TRADE_ROWS>"] for name in COMPONENTS}
                components = {name: None for name in COMPONENTS}
                residual = None
                status = "INCOMPLETE"
            realized = None if ledger_value is None else float(ledger_value)
            reports.append({
                "day": day, "trade_count": 0, "status": status,
                "components_inr": components, "realized_net_inr": realized,
                "portfolio_loss_inr": max(0.0, -realized) if realized is not None else None,
                "unexplained_residual_inr": residual,
                "daily_ledger_residual_inr": 0.0 if realized == 0 else None,
                "missing_trade_ids_by_component": day_missing,
            })
            continue
        totals = {name: 0.0 for name in REQUIRED}
        missing: dict[str, list[str]] = {name: [] for name in REQUIRED}
        for row in day_rows:
            trade_id = str(row["trade_id"])
            for name in REQUIRED:
                value = row.get(name)
                if value is None:
                    missing[name].append(trade_id)
                    continue
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise LossAttributionError(f"{name} must be a finite INR number or null")
                totals[name] += float(value)

        missing = {name: ids for name, ids in missing.items() if ids}
        known_components = sum(totals[name] for name in COMPONENTS)
        realized_known = totals["realized_net_inr"] if "realized_net_inr" not in missing else None
        residual = (realized_known - known_components
                    if realized_known is not None and not any(name in missing for name in COMPONENTS)
                    else None)
        reconciled = not missing and residual is not None and abs(residual) <= tolerance_inr
        net = realized_known
        ledger_residual = None
        if ledger_value is not None and realized_known is not None:
            ledger_residual = float(ledger_value) - realized_known
            if abs(ledger_residual) > tolerance_inr:
                reconciled = False
            net = float(ledger_value)
        elif days is not None and ledger_value is None:
            reconciled = False
            net = None
        reports.append({
            "day": day,
            "trade_count": len(day_rows),
            "status": "RECONCILED" if reconciled else "INCOMPLETE",
            "components_inr": {name: (None if name in missing else totals[name]) for name in COMPONENTS},
            "realized_net_inr": net,
            "portfolio_loss_inr": max(0.0, -net) if net is not None else None,
            "unexplained_residual_inr": residual,
            "daily_ledger_residual_inr": ledger_residual,
            "missing_trade_ids_by_component": missing,
        })
    return {
        "status": "RECONCILED" if all(day["status"] == "RECONCILED" for day in reports) else "INCOMPLETE",
        "days": reports,
        "tolerance_inr": tolerance_inr,
        "sign_convention": "All effects are signed INR contributions to net P&L; expenses are negative.",
        "counterfactual_basis": "Caller-declared; the function checks arithmetic, not causal validity.",
    }
