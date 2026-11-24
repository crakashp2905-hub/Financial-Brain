"""Paper-only portfolio exit guard for aggregate loss and option exposure limits.

This module proposes risk-reducing quantities; it never creates or submits orders.
Exchange instruments require an executable quote and visible depth. Mutual funds
produce NAV/cutoff review requests because they do not have intraday exchange fills.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Literal, TypedDict


class PortfolioExitGuardError(ValueError):
    """Invalid portfolio snapshot or risk policy."""


class _OptionGreeks(TypedDict):
    delta: Decimal
    gamma: Decimal
    vega: Decimal


class _NormalizedPosition(TypedDict):
    position_id: str
    instrument_id: str
    asset_class: str
    quantity: Decimal
    quantity_step: Decimal
    unit_stop_risk_inr: Decimal
    current_pnl_inr: Decimal
    risk_factor: str | None
    greeks: _OptionGreeks
    quote_executable: bool
    visible_exit_quantity: Decimal


class _NormalizedLimits(TypedDict):
    stop: Decimal
    factors: dict[str, Decimal]
    greek_caps: dict[str, Decimal]


_GREEK_NAMES: tuple[Literal["delta", "gamma", "vega"], ...] = ("delta", "gamma", "vega")


def _decimal(value: Decimal | str | int, label: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PortfolioExitGuardError(f"{label} must be a decimal") from exc
    if not result.is_finite():
        raise PortfolioExitGuardError(f"{label} must be finite")
    return result


@dataclass(frozen=True)
class GuardPosition:
    position_id: str
    instrument_id: str
    asset_class: Literal["EQUITY", "ETF", "OPTION", "MUTUAL_FUND"]
    quantity: Decimal | str | int
    quantity_step: Decimal | str | int = Decimal("1")
    unit_stop_risk_inr: Decimal | str | int = Decimal("0")
    current_pnl_inr: Decimal | str | int = Decimal("0")
    risk_factor: str | None = None
    option_delta_exposure_inr: Decimal | str | int = Decimal("0")
    option_gamma_exposure_inr: Decimal | str | int = Decimal("0")
    option_vega_exposure_inr: Decimal | str | int = Decimal("0")
    quote_executable: bool = False
    visible_exit_quantity: Decimal | str | int = Decimal("0")

    def normalized(self) -> _NormalizedPosition:
        if not self.position_id.strip() or not self.instrument_id.strip():
            raise PortfolioExitGuardError("position and instrument identities are required")
        if self.asset_class not in {"EQUITY", "ETF", "OPTION", "MUTUAL_FUND"}:
            raise PortfolioExitGuardError("unsupported asset class")
        quantity = _decimal(self.quantity, "quantity")
        step = _decimal(self.quantity_step, "quantity_step")
        stop_risk = _decimal(self.unit_stop_risk_inr, "unit_stop_risk_inr")
        pnl = _decimal(self.current_pnl_inr, "current_pnl_inr")
        visible = _decimal(self.visible_exit_quantity, "visible_exit_quantity")
        greeks: _OptionGreeks = {
            "delta": _decimal(self.option_delta_exposure_inr, "option_delta_exposure_inr"),
            "gamma": _decimal(self.option_gamma_exposure_inr, "option_gamma_exposure_inr"),
            "vega": _decimal(self.option_vega_exposure_inr, "option_vega_exposure_inr"),
        }
        if quantity <= 0 or step <= 0 or stop_risk < 0 or visible < 0:
            raise PortfolioExitGuardError("quantity/step must be positive; risk and visible depth non-negative")
        if quantity % step != 0:
            raise PortfolioExitGuardError("position quantity must align to its exchange lot or NAV unit step")
        if self.asset_class != "OPTION" and any(greeks.values()):
            raise PortfolioExitGuardError("option Greek exposures are only valid for option positions")
        if self.asset_class == "MUTUAL_FUND" and self.quote_executable:
            raise PortfolioExitGuardError("mutual-fund units cannot use an exchange executable quote")
        return {
            "position_id": self.position_id,
            "instrument_id": self.instrument_id,
            "asset_class": self.asset_class,
            "quantity": quantity,
            "quantity_step": step,
            "unit_stop_risk_inr": stop_risk,
            "current_pnl_inr": pnl,
            "risk_factor": self.risk_factor.strip() if self.risk_factor else None,
            "greeks": greeks,
            "quote_executable": self.quote_executable,
            "visible_exit_quantity": visible,
        }


@dataclass(frozen=True)
class ExitGuardLimits:
    max_open_stop_risk_inr: Decimal | str | int
    max_correlated_loss_inr_by_factor: dict[str, Decimal | str | int] = field(default_factory=dict)
    max_gross_option_delta_inr: Decimal | str | int | None = None
    max_gross_option_gamma_inr: Decimal | str | int | None = None
    max_gross_option_vega_inr: Decimal | str | int | None = None

    def normalized(self) -> _NormalizedLimits:
        stop = _decimal(self.max_open_stop_risk_inr, "max_open_stop_risk_inr")
        if stop <= 0:
            raise PortfolioExitGuardError("maximum open stop risk must be positive")
        factors = {}
        for name, value in self.max_correlated_loss_inr_by_factor.items():
            if not name.strip():
                raise PortfolioExitGuardError("correlated-risk factor names are required")
            cap = _decimal(value, f"correlated loss limit for {name}")
            if cap <= 0:
                raise PortfolioExitGuardError("correlated loss limits must be positive")
            factors[name.strip()] = cap
        greek_caps = {}
        for name, raw_cap in (("delta", self.max_gross_option_delta_inr),
                              ("gamma", self.max_gross_option_gamma_inr),
                              ("vega", self.max_gross_option_vega_inr)):
            if raw_cap is not None:
                cap = _decimal(raw_cap, f"max_gross_option_{name}_inr")
                if cap <= 0:
                    raise PortfolioExitGuardError("option Greek limits must be positive")
                greek_caps[name] = cap
        return {"stop": stop, "factors": factors, "greek_caps": greek_caps}


@dataclass(frozen=True)
class ExitProposal:
    position_id: str
    instrument_id: str
    asset_class: str
    action: str
    status: str
    requested_quantity: Decimal
    executable_quantity: Decimal
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class ExitGuardResult:
    total_open_stop_risk_inr: Decimal
    correlated_loss_inr_by_factor: dict[str, Decimal]
    gross_option_exposure_inr: dict[str, Decimal]
    proposals: tuple[ExitProposal, ...]
    physical_orders_submitted: bool = False


def evaluate_portfolio_exit_guard(*, positions: tuple[GuardPosition, ...], limits: ExitGuardLimits) -> ExitGuardResult:
    """Identify breach-driven, lot/depth-aware paper exit proposals without executing orders."""
    policy = limits.normalized()
    rows: list[_NormalizedPosition] = [position.normalized() for position in positions]
    if len({str(row["position_id"]) for row in rows}) != len(rows):
        raise PortfolioExitGuardError("position IDs must be unique")

    total_stop_risk = sum((row["quantity"] * row["unit_stop_risk_inr"] for row in rows), Decimal(0))
    correlated_losses: dict[str, Decimal] = {}
    for row in rows:
        factor = row["risk_factor"]
        if factor:
            correlated_losses[factor] = correlated_losses.get(factor, Decimal(0)) + max(
                Decimal(0), -row["current_pnl_inr"])
    option_exposure: dict[str, Decimal] = {
        greek: sum((abs(row["greeks"][greek]) for row in rows if row["asset_class"] == "OPTION"), Decimal(0))
        for greek in _GREEK_NAMES
    }

    scales: dict[str, tuple[Decimal, set[str]]] = {
        str(row["position_id"]): (Decimal(1), set()) for row in rows
    }

    def apply_scale(row: _NormalizedPosition, ratio: Decimal, reason: str) -> None:
        key = str(row["position_id"])
        previous, reasons = scales[key]
        scales[key] = (min(previous, max(Decimal(0), min(Decimal(1), ratio))), reasons | {reason})

    stop_cap = policy["stop"]
    if total_stop_risk > stop_cap:
        ratio = stop_cap / total_stop_risk
        for row in rows:
            apply_scale(row, ratio, "AGGREGATE_STOP_RISK_LIMIT")

    factor_caps = policy["factors"]
    for factor, cap in factor_caps.items():
        loss = correlated_losses.get(factor, Decimal(0))
        if loss > cap:
            ratio = cap / loss
            for row in rows:
                if row["risk_factor"] == factor and row["current_pnl_inr"] < 0:
                    apply_scale(row, ratio, f"CORRELATED_LOSS_LIMIT:{factor}")

    greek_caps = policy["greek_caps"]
    for greek in _GREEK_NAMES:
        if greek not in greek_caps:
            continue
        cap = greek_caps[greek]
        exposure = option_exposure[greek]
        if exposure > cap:
            ratio = cap / exposure
            for row in rows:
                if row["asset_class"] == "OPTION" and row["greeks"][greek] != 0:
                    apply_scale(row, ratio, f"OPTION_{greek.upper()}_LIMIT")

    proposals: list[ExitProposal] = []
    for row in rows:
        scale, reasons = scales[str(row["position_id"])]
        if not reasons:
            continue
        quantity: Decimal = row["quantity"]
        step: Decimal = row["quantity_step"]
        retained = ((quantity * scale / step).to_integral_value(rounding=ROUND_DOWN) * step)
        requested = min(quantity, quantity - retained)
        if requested <= 0:
            proposals.append(ExitProposal(str(row["position_id"]), str(row["instrument_id"]),
                str(row["asset_class"]), "NO_ACTION", "BELOW_MINIMUM_LOT", Decimal(0), Decimal(0),
                tuple(sorted(reasons | {"REDUCTION_BELOW_MINIMUM_QUANTITY_STEP"}))))
            continue
        if row["asset_class"] == "MUTUAL_FUND":
            proposals.append(ExitProposal(str(row["position_id"]), str(row["instrument_id"]),
                "MUTUAL_FUND", "NAV_REDEMPTION_REVIEW", "REVIEW_CUTOFF_AND_SETTLEMENT", requested,
                Decimal(0), tuple(sorted(reasons | {"NAV_BASED_EXECUTION_ONLY"}))))
            continue
        if not row["quote_executable"]:
            proposals.append(ExitProposal(str(row["position_id"]), str(row["instrument_id"]),
                str(row["asset_class"]), "DEFERRED", "NO_EXECUTABLE_QUOTE", requested, Decimal(0),
                tuple(sorted(reasons | {"NO_EXECUTABLE_EXIT_QUOTE"}))))
            continue
        depth: Decimal = row["visible_exit_quantity"]
        executable = min(requested, (depth / step).to_integral_value(rounding=ROUND_DOWN) * step)
        status = "READY_FOR_PAPER_REVIEW" if executable == requested else "DEPTH_LIMITED"
        if executable == 0:
            status = "NO_VISIBLE_DEPTH"
        proposal_reasons = reasons if executable == requested else reasons | {"VISIBLE_DEPTH_LIMIT"}
        proposals.append(ExitProposal(str(row["position_id"]), str(row["instrument_id"]),
            str(row["asset_class"]), "PAPER_EXIT_REVIEW" if executable > 0 else "DEFERRED", status,
            requested, executable, tuple(sorted(proposal_reasons))))

    return ExitGuardResult(total_stop_risk, correlated_losses, option_exposure, tuple(proposals), False)
