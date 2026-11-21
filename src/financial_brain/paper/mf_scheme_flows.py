"""Apply MF economic flows to paper units/cash without treating NAV jumps as alpha."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from ..forward_edge.data_permissions import DataPermission
from ..kernel.trading_clock import IST
from ..providers.mf_scheme_flows import SchemeEconomicFlow, SchemeFlowError, SchemeFlowKind


def _amount(value: Decimal | str | int, label: str, *, allow_zero: bool = False) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SchemeFlowError(f"{label} must be decimal") from exc
    if not result.is_finite() or result < 0 or (result == 0 and not allow_zero):
        raise SchemeFlowError(f"{label} must be {'non-negative' if allow_zero else 'positive'}")
    return result


@dataclass(frozen=True)
class MfPaperPosition:
    scheme_code: str
    isin: str
    units: Decimal | str | int
    cash_inr: Decimal | str | int = Decimal(0)
    applied_event_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.scheme_code.strip() or not self.isin.strip():
            raise SchemeFlowError("position scheme_code and ISIN are required")
        object.__setattr__(self, "units", _amount(self.units, "units", allow_zero=True))
        object.__setattr__(self, "cash_inr", _amount(self.cash_inr, "cash", allow_zero=True))
        if len(self.applied_event_ids) != len(set(self.applied_event_ids)):
            raise SchemeFlowError("applied event IDs must be unique")


@dataclass(frozen=True)
class MfFlowApplication:
    position: MfPaperPosition
    event_id: str
    event_applied: bool
    value_before_inr: Decimal | None
    value_after_inr: Decimal | None
    total_return_percent: Decimal | None


def apply_flow(
    position: MfPaperPosition,
    flow: SchemeEconomicFlow,
    *,
    decision_at: datetime,
    nav_before: Decimal | str | int,
    nav_after: Decimal | str | int,
    permissions: tuple[DataPermission, ...],
) -> MfFlowApplication:
    """Apply a known event exactly once and reconcile position value across NAVs."""
    if decision_at.tzinfo is None or decision_at.utcoffset() is None:
        raise ValueError("decision_at must be timezone-aware")
    flow.require_permissions(permissions)
    local_decision_date = decision_at.astimezone(IST).date()
    if (flow.published_at > decision_at or flow.observed_at > decision_at
            or flow.effective_date > local_decision_date):
        raise SchemeFlowError("flow is not yet available to this decision")
    if position.scheme_code != flow.scheme_code or position.isin != flow.isin:
        raise SchemeFlowError("flow identity does not match the held scheme")
    if flow.event_id in position.applied_event_ids:
        return MfFlowApplication(position, flow.event_id, False, None, None, None)

    nav0 = _amount(nav_before, "NAV before")
    nav1 = _amount(nav_after, "NAV after")
    start_units = Decimal(position.units)
    start_cash = Decimal(position.cash_inr)
    units_after, cash_after = start_units, start_cash
    if flow.kind == SchemeFlowKind.IDCW_PAYOUT:
        if flow.cash_per_unit is None:
            raise SchemeFlowError("IDCW payout amount is missing")
        cash_after += start_units * _amount(flow.cash_per_unit, "cash per unit", allow_zero=True)
    elif flow.kind == SchemeFlowKind.IDCW_REINVESTMENT:
        if flow.cash_per_unit is None or flow.reinvestment_nav is None:
            raise SchemeFlowError("IDCW reinvestment terms are incomplete")
        distribution = start_units * _amount(flow.cash_per_unit, "cash per unit", allow_zero=True)
        units_after += distribution / _amount(flow.reinvestment_nav, "reinvestment NAV")
    elif flow.kind == SchemeFlowKind.UNIT_SPLIT:
        if flow.unit_ratio is None:
            raise SchemeFlowError("unit split ratio is missing")
        units_after *= _amount(flow.unit_ratio, "unit ratio")
    elif flow.kind == SchemeFlowKind.SCHEME_MERGER:
        if flow.unit_ratio is None:
            raise SchemeFlowError("scheme merger ratio is missing")
        units_after *= _amount(flow.unit_ratio, "unit ratio")
    # Cash ledger value is expressed in INR paise. Rounding before return attribution
    # prevents a repeating fractional-unit division from creating phantom NAV P&L.
    value_before = (start_units * nav0 + start_cash).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    value_after = (units_after * nav1 + cash_after).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    if value_before <= 0:
        raise SchemeFlowError("position value before event must be positive")
    total_return = (value_after / value_before - Decimal(1)) * Decimal(100)
    next_scheme = flow.successor_scheme_code or position.scheme_code
    next_isin = flow.successor_isin or position.isin
    updated = MfPaperPosition(
        scheme_code=next_scheme, isin=next_isin, units=units_after,
        cash_inr=cash_after,
        applied_event_ids=position.applied_event_ids + (flow.event_id,),
    )
    return MfFlowApplication(updated, flow.event_id, True, value_before,
                             value_after, total_return)


__all__ = ["MfFlowApplication", "MfPaperPosition", "apply_flow"]
