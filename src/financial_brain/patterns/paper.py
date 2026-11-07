"""Paper-only intents, cost-aware fills, cash ledger and deterministic risk limits (stages 12-13).

Boundary: this module never imports the OMS, a broker client, or the live paper
engine. A :class:`PatternIntent` is research output that a paper simulator may
consume; it carries no broker authorisation and no order identifier.

Economics follow the checklist equations:
- ``R_net = (V_exit - V_entry - costs) / V_entry`` via :func:`net_return`.
- Equity identity ``E = cash + sum(q*P) - liabilities`` via :meth:`Ledger.equity`.
Fills are labelled ``ASSUMED``; nothing here claims an observed broker match.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal

from financial_brain.patterns.spec import canonical_json, sha256_hex


class IntentRefused(ValueError):
    """An intent violates chronology, eligibility or limits."""


@dataclass(frozen=True)
class PatternIntent:
    event_id: str
    spec_sha256: str
    data_sha256: str
    manifest_sha256: str
    symbol: str
    side: Literal["long", "short"]
    quantity: int
    decision_at_utc: str
    earliest_entry_at_utc: str
    segment: Literal["cash_equity", "futures_fixture"]
    paper_only: bool = True

    def __post_init__(self) -> None:
        if not self.paper_only:
            raise IntentRefused("pattern intents are paper-only")
        if self.quantity <= 0:
            raise IntentRefused("quantity must be positive")
        if self.side == "short" and self.segment != "futures_fixture":
            raise IntentRefused("shorts are allowed only on explicitly supported futures fixtures (CP-0207)")
        if datetime.fromisoformat(self.decision_at_utc) > datetime.fromisoformat(self.earliest_entry_at_utc):
            raise IntentRefused("decision must not follow entry (CP-0141)")


def next_session_entry(confirmed_index: int, sessions: list[int]) -> int | None:
    """First executable session strictly after confirmation; None if none exists (CP-0190)."""
    later = [s for s in sessions if s > confirmed_index]
    return later[0] if later else None


@dataclass(frozen=True)
class CostModel:
    spread_bps: float
    fee_bps: float
    impact_bps: float
    financing_bps_per_day: float

    def __post_init__(self) -> None:
        for v in (self.spread_bps, self.fee_bps, self.impact_bps, self.financing_bps_per_day):
            if not math.isfinite(v) or v < 0:
                raise ValueError("cost components must be finite and non-negative")


def cost_breakdown(notional: float, model: CostModel, *, holding_days: int) -> dict[str, float]:
    """Each modelled charge separately; net value is gross minus all of them (CP-0192)."""
    if notional <= 0 or holding_days < 0:
        raise ValueError("notional must be positive and holding_days non-negative")
    parts = {
        "spread": notional * model.spread_bps / 1e4,
        "fee": notional * model.fee_bps / 1e4,
        "impact": notional * model.impact_bps / 1e4,
        "financing": notional * model.financing_bps_per_day * holding_days / 1e4,
    }
    parts["total"] = sum(parts.values())
    return parts


def net_return(entry_price: float, exit_price: float, costs_total: float, notional: float) -> float:
    """R_net from the checklist: (V_exit - V_entry - costs) / V_entry with V_entry = notional."""
    if entry_price <= 0 or notional <= 0:
        raise ValueError("prices and notional must be positive")
    shares = notional / entry_price
    gross = shares * (exit_price - entry_price)
    return (gross - costs_total) / notional


class FillOutcome(str, Enum):
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    NO_FILL = "NO_FILL"
    REJECTED = "REJECTED"
    TIMEOUT = "TIMEOUT"
    STALE_QUOTE = "STALE_QUOTE"


@dataclass(frozen=True)
class FillRecord:
    outcome: FillOutcome
    requested: int
    filled: int
    label: str = "ASSUMED"  # simulated fills are never presented as observed broker matches (CP-0204)


def simulate_fill(outcome: FillOutcome, requested: int, *, partial_fraction: float = 0.5) -> FillRecord:
    """Deterministic, explicitly labelled fill outcome; no silent full fills (CP-0193)."""
    if requested <= 0:
        raise ValueError("requested must be positive")
    if outcome is FillOutcome.FILLED:
        filled = requested
    elif outcome is FillOutcome.PARTIAL:
        if not 0 < partial_fraction < 1:
            raise ValueError("partial_fraction must be in (0,1)")
        filled = max(1, int(requested * partial_fraction))
    else:
        filled = 0
    return FillRecord(outcome, requested, filled)


@dataclass
class Ledger:
    """Cash, positions and reservations. Negative cash and hidden leverage are refused (CP-0194)."""

    cash: float
    positions: dict[str, tuple[int, float]] = field(default_factory=dict)  # symbol -> (qty, last price)
    reserved: float = 0.0
    pending: dict[str, int] = field(default_factory=dict)  # intents, never holdings (CP-0197)
    liabilities: float = 0.0

    def available(self) -> float:
        return self.cash - self.reserved

    def reserve(self, amount: float) -> None:
        if amount <= 0:
            raise ValueError("reservation must be positive")
        if amount > self.available():
            raise IntentRefused("insufficient buying power; reservation refused")
        self.reserved += amount

    def fill_buy(self, symbol: str, record: FillRecord, price: float, cost: float) -> None:
        if record.filled == 0:
            self.pending.pop(symbol, None)
            return
        spend = record.filled * price + cost
        if spend > self.cash:
            raise IntentRefused("fill would make cash negative")
        self.cash -= spend
        qty, _ = self.positions.get(symbol, (0, price))
        self.positions[symbol] = (qty + record.filled, price)
        self.reserved = max(0.0, self.reserved - spend)

    def mark(self, symbol: str, price: float) -> None:
        qty, _ = self.positions.get(symbol, (0, price))
        self.positions[symbol] = (qty, price)

    def equity(self) -> float:
        return self.cash + sum(q * p for q, p in self.positions.values()) - self.liabilities


def ledger_identity_holds(opening: float, flows: float, realized: float, unrealized: float, ending: float,
                          *, tol: float = 1e-6) -> bool:
    """opening + flows + realized + unrealized = ending within tolerance (CP-0202)."""
    return abs(opening + flows + realized + unrealized - ending) <= tol


@dataclass(frozen=True)
class RiskLimits:
    max_symbol_weight: float = 0.10
    max_sector_weight: float = 0.25
    max_drawdown: float = 0.15
    max_leverage: float = 1.0


@dataclass
class KillSwitch:
    active: bool = False

    def allows_new_entries(self) -> bool:
        return not self.active  # existing positions may still exit (CP-0216)


def check_new_intent(
    *, limits: RiskLimits, symbol_weight_after: float, sector_weight_after: float,
    drawdown: float, leverage_after: float, eligible: bool, kill: KillSwitch,
) -> list[str]:
    """Machine-readable refusal codes; an empty list means the paper intent may proceed (CP-0195)."""
    reasons: list[str] = []
    if not eligible:
        reasons.append("INELIGIBLE_SESSION")
    if not kill.allows_new_entries():
        reasons.append("KILL_SWITCH_ACTIVE")
    if symbol_weight_after > limits.max_symbol_weight:
        reasons.append("SYMBOL_LIMIT")
    if sector_weight_after > limits.max_sector_weight:
        reasons.append("SECTOR_LIMIT")
    if drawdown > limits.max_drawdown:
        reasons.append("DRAWDOWN_LIMIT")
    if leverage_after > limits.max_leverage:
        reasons.append("LEVERAGE_LIMIT")
    return reasons


def session_eligibility(*, circuit: bool, suspended: bool, auction_only: bool) -> bool:
    """No synthetic executable trade while the market is ineligible (CP-0196)."""
    return not (circuit or suspended or auction_only)


def trade_summary(pnl_by_day: dict[str, float]) -> dict[str, float | int]:
    """Zero-trade days show zero realised P&L, not a success (CP-0200)."""
    days = len(pnl_by_day)
    total = sum(pnl_by_day.values())
    trading_days = sum(1 for v in pnl_by_day.values() if v != 0)
    return {"days": days, "trading_days": trading_days, "realized_total": total}


def drawdown(equity_curve: list[float]) -> float:
    """Maximum peak-to-trough fraction on an equity curve (CP-0203)."""
    peak = -math.inf
    worst = 0.0
    for x in equity_curve:
        peak = max(peak, x)
        if peak > 0:
            worst = max(worst, (peak - x) / peak)
    return worst


def profit_factor(trade_pnls: list[float]) -> float | None:
    """Gross profit over gross loss; None when there are no losing trades (CP-0203)."""
    gains = sum(p for p in trade_pnls if p > 0)
    losses = -sum(p for p in trade_pnls if p < 0)
    return None if losses == 0 else gains / losses


LOSS_CATEGORIES = ("gap_through_stop", "false_breakout", "stale_quote", "spread", "other")


def classify_loss(*, gap_through_stop: bool, false_breakout: bool, stale_quote: bool, spread_dominant: bool) -> str:
    """One explicit category per losing trade, in a fixed priority order (CP-0273)."""
    if gap_through_stop:
        return "gap_through_stop"
    if stale_quote:
        return "stale_quote"
    if false_breakout:
        return "false_breakout"
    if spread_dominant:
        return "spread"
    return "other"


def paper_record(intent: PatternIntent, fill: FillRecord, costs: dict[str, float]) -> dict[str, object]:
    """Trade record linked to its frozen spec, data and manifest hashes (CP-0201)."""
    return {
        "event_id": intent.event_id,
        "spec_sha256": intent.spec_sha256,
        "data_sha256": intent.data_sha256,
        "manifest_sha256": intent.manifest_sha256,
        "outcome": fill.outcome.value,
        "filled": fill.filled,
        "fill_label": fill.label,
        "costs_total": costs.get("total", 0.0),
    }


def check_intent_codes(*, stale_bars: bool, ambiguous_geometry: bool, low_liquidity: bool,
                       insufficient_evidence: bool, missing_calibration: bool) -> list[str]:
    """Machine-readable no-trade reasons beyond the risk limits (CP-0206)."""
    codes = []
    if stale_bars:
        codes.append("STALE_BARS")
    if ambiguous_geometry:
        codes.append("AMBIGUOUS_GEOMETRY")
    if low_liquidity:
        codes.append("LOW_LIQUIDITY")
    if insufficient_evidence:
        codes.append("INSUFFICIENT_EVIDENCE")
    if missing_calibration:
        codes.append("MISSING_CALIBRATION")
    return codes


def risk_display(*, daily_vol: float, gap_risk: float, max_adverse_excursion: float) -> dict[str, object]:
    """Risk shown separately from any stop; a stop is never presented as a loss cap (CP-0208)."""
    return {
        "daily_volatility": daily_vol,
        "gap_risk": gap_risk,
        "max_adverse_excursion": max_adverse_excursion,
        "stop_caps_loss": False,
        "note": "no guarantee: gaps and halts can exceed any stop",
    }


def loss_if_gap(*, entry: float, stop: float, open_price: float, quantity: int) -> dict[str, float]:
    """Long position: nominal stop loss versus the loss when the session opens through the stop (CP-0209).

    A stop fills at the stop only if the market trades through it intraday. A gap that opens below the stop fills
    at the open, so the actual loss can exceed the nominal stop loss.
    """
    if min(entry, stop, open_price) <= 0 or quantity <= 0:
        raise ValueError("prices and quantity must be positive")
    if stop >= entry:
        raise ValueError("a long stop must sit below entry")
    nominal = (entry - stop) * quantity
    fill = min(open_price, stop) if open_price < stop else stop
    actual = (entry - fill) * quantity
    return {"nominal_loss": nominal, "actual_loss": actual, "exceeds_nominal": float(actual > nominal + 1e-12)}


def participation_ok(order_qty: int, adv_qty: float, *, cap: float = 0.05) -> bool:
    """ADV participation cap on point-in-time volume; unknown ADV refuses (CP-0210)."""
    if adv_qty <= 0:
        return False
    return order_qty / adv_qty <= cap


def correlated_exposure_ok(same_symbol_positions: int, *, cap: int = 1) -> bool:
    """Five overlapping bullish shapes on one symbol do not multiply the intended position (CP-0211)."""
    return same_symbol_positions <= cap


STRESS_CASH_SCENARIOS = frozenset({"bear_market", "shock", "market_suspension", "spread_blowout"})


def stress_action(scenario: str) -> str:
    """Predeclared stress failures degrade to cash or no-trade (CP-0212)."""
    return "CASH_NO_TRADE" if scenario in STRESS_CASH_SCENARIOS else "NORMAL"


def atr_size(*, risk_budget: float, atr_value: float, k: float = 2.0) -> int:
    """Position from volatility and risk budget only; no historical success rate is an input (CP-0213)."""
    if risk_budget <= 0 or atr_value <= 0 or k <= 0:
        raise ValueError("inputs must be positive")
    return int(risk_budget // (k * atr_value))


def risk_adjusted_utility(net_return: float, risk_penalty: float) -> dict[str, float | str]:
    """Utility is a separate output from economic net return and is labelled as such (CP-0214)."""
    return {"net_return": net_return, "risk_adjusted_utility": net_return - risk_penalty, "label": "UTILITY_NOT_PROFIT"}


def sector_hhi(weights: dict[str, float]) -> float:
    """Herfindahl concentration across sectors (CP-0215)."""
    total = sum(weights.values())
    if total <= 0:
        return 0.0
    return sum((w / total) ** 2 for w in weights.values())


def evidence_bundle(intent: PatternIntent, fill: FillRecord, costs: dict[str, float], ledger_sha256: str) -> dict[str, object]:
    """Full evidence for one paper intent: spec, event, fill model and ledger hashes (CP-0386)."""
    body: dict[str, object] = dict(paper_record(intent, fill, costs))
    body["ledger_sha256"] = ledger_sha256
    body["bundle_sha256"] = sha256_hex(canonical_json(body))
    return body


def fill_coverage(records: list[dict[str, object]]) -> dict[str, dict[str, int]]:
    """Fill records per strategy and day, with how many carry the ASSUMED label (CP-0204)."""
    out: dict[str, dict[str, int]] = {}
    for rec in records:
        key = f"{rec['strategy']}|{rec['day']}"
        bucket = out.setdefault(key, {"total": 0, "assumed": 0})
        bucket["total"] += 1
        if rec.get("fill_label") == "ASSUMED":
            bucket["assumed"] += 1
    return dict(sorted(out.items()))
