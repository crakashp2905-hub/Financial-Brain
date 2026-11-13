"""Offline, conservative paper execution model (FE-0055 .. FE-0068). Simulation only: nothing here reaches a broker.

Rules enforced:
* A fill may use only quotes available at or after ``decision + latency`` (no earlier prices, no hindsight range).
* A marketable buy fills at the ask, a marketable sell at the bid, tick-rounded against the trader, capped by the
  visible size. Mid-price fills do not exist.
* A passive limit fills only when a trade prints strictly through the limit after the decision; touching is not a
  fill, and the unknown queue position is disclosed on every result.
* Partial fills, price-band rejects and freeze-quantity slicing are explicit outcomes. NO_TRADE and MISSED are
  recorded states, never missing rows.
* Fees come from an effective-dated schedule supplied as data (no rates hardcoded here); rounding to paise happens
  once, at the end.
* Receipts carry their fidelity (SIMULATED_QUOTE, SIMULATED_BAR, OBSERVED_BROKER); fidelities are never summed together.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal

CENT = Decimal("0.01")


class ExecutionError(ValueError):
    pass


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ExecutionError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class Quote:
    available_utc: datetime
    bid: Decimal
    ask: Decimal
    bid_qty: int
    ask_qty: int


@dataclass(frozen=True)
class OrderIntent:
    instrument: str
    side: str  # BUY or SELL
    quantity: int
    decision_utc: datetime
    tick: Decimal
    limit: Decimal | None = None

    def __post_init__(self) -> None:
        if self.side not in ("BUY", "SELL"):
            raise ExecutionError("side must be BUY or SELL")
        if type(self.quantity) is not int or self.quantity <= 0:
            raise ExecutionError("quantity must be a positive integer")
        if self.tick <= 0:
            raise ExecutionError("tick must be positive")
        _aware(self.decision_utc)


@dataclass(frozen=True)
class ExecResult:
    status: str  # FILLED, PARTIAL, MISSED, REJECTED, NO_TRADE
    filled_qty: int
    avg_price: Decimal | None
    reason: str
    fidelity: str = "SIMULATED_QUOTE"
    queue_position_known: bool = True


# ---- FE-0055: one canonical intent, whichever engine produced the signal ----

def intent_from_signal(signal: dict, *, tick: Decimal) -> OrderIntent:
    """Adapter: a plain signal dict (from any engine) becomes the single canonical order intent."""
    return OrderIntent(instrument=str(signal["instrument"]), side=str(signal["side"]), quantity=int(signal["quantity"]),
                       decision_utc=signal["decision_utc"], tick=tick,
                       limit=Decimal(str(signal["limit"])) if signal.get("limit") is not None else None)


# ---- FE-0056 .. FE-0059 ----

def round_to_tick(price: Decimal, tick: Decimal, *, against_buyer: bool) -> Decimal:
    mode = ROUND_CEILING if against_buyer else ROUND_FLOOR
    return (price / tick).to_integral_value(rounding=mode) * tick


def earliest_quote(quotes: list[Quote], decision_utc: datetime, latency: timedelta) -> Quote | None:
    cutoff = _aware(decision_utc) + latency
    later = [q for q in quotes if _aware(q.available_utc) >= cutoff]
    return min(later, key=lambda q: _aware(q.available_utc)) if later else None


def quote_arrival_shortfall(*, side: str, modeled_price: Decimal, requested_qty: int,
                            decision_utc: datetime, latency: timedelta, quote: Quote,
                            source_sha256: str) -> dict:
    """Compare a frozen model price with the first executable quote at modeled order arrival.

    This measures quote movement/availability, not a broker fill. Only displayed quantity is measured; no price is
    extrapolated beyond top-of-book depth. The source event hash is retained for audit and replay.
    """
    if side not in {"BUY", "SELL"}:
        raise ExecutionError("side must be BUY or SELL")
    if not isinstance(modeled_price, Decimal) or not modeled_price.is_finite() or modeled_price <= 0:
        raise ExecutionError("modeled price must be a positive finite Decimal")
    if type(requested_qty) is not int or requested_qty <= 0:
        raise ExecutionError("requested quantity must be a positive integer")
    if not isinstance(latency, timedelta) or latency < timedelta(0):
        raise ExecutionError("latency must be non-negative")
    if not isinstance(source_sha256, str) or len(source_sha256) != 64 or any(
        c not in "0123456789abcdef" for c in source_sha256
    ):
        raise ExecutionError("source quote SHA-256 is required")
    cutoff = _aware(decision_utc) + latency
    available = _aware(quote.available_utc)
    if available < cutoff:
        raise ExecutionError("quote is not available at modeled order arrival")
    if (not isinstance(quote.bid, Decimal) or not isinstance(quote.ask, Decimal)
            or not quote.bid.is_finite() or not quote.ask.is_finite() or quote.bid <= 0
            or quote.ask < quote.bid or type(quote.bid_qty) is not int or type(quote.ask_qty) is not int
            or quote.bid_qty < 0 or quote.ask_qty < 0):
        raise ExecutionError("arrival quote is invalid")
    executable_price = quote.ask if side == "BUY" else quote.bid
    displayed_qty = quote.ask_qty if side == "BUY" else quote.bid_qty
    measured_qty = min(requested_qty, displayed_qty)
    if measured_qty <= 0:
        raise ExecutionError("arrival quote has no executable displayed quantity")
    sign = Decimal(1) if side == "BUY" else Decimal(-1)
    shortfall_bp = sign * (executable_price / modeled_price - Decimal(1)) * Decimal(10_000)
    return {
        "benchmark_type": "SUBSEQUENT_EXECUTABLE_QUOTE",
        "fidelity": "SIMULATED_QUOTE",
        "broker_fill_observation": False,
        "side": side,
        "modeled_price": str(modeled_price),
        "arrival_executable_price": str(executable_price),
        "decision_available_utc": _aware(decision_utc).isoformat(),
        "order_eligible_utc": cutoff.isoformat(),
        "quote_available_utc": available.isoformat(),
        "requested_qty": requested_qty,
        "measured_qty": measured_qty,
        "depth_coverage": measured_qty / requested_qty,
        "source_quote_sha256": source_sha256,
        "shortfall_bp": str(shortfall_bp),
    }


def quote_arrival_shortfall_report(rows: list[dict]) -> dict:
    """Aggregate quote-arrival shortfall while explicitly excluding broker-fill claims."""
    if not rows:
        return {"benchmark_type": "SUBSEQUENT_EXECUTABLE_QUOTE", "fidelity": "SIMULATED_QUOTE",
                "broker_fill_observations": 0, "samples": 0, "mean_shortfall_bp": None,
                "depth_coverage": 0.0}
    for row in rows:
        if (row.get("benchmark_type") != "SUBSEQUENT_EXECUTABLE_QUOTE"
                or row.get("fidelity") != "SIMULATED_QUOTE" or row.get("broker_fill_observation") is not False):
            raise ExecutionError("quote-arrival report cannot include broker-fill or unlabeled rows")
        digest = row.get("source_quote_sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ExecutionError("quote-arrival report requires source quote hashes")
        if (type(row.get("requested_qty")) is not int or type(row.get("measured_qty")) is not int
                or row["requested_qty"] <= 0 or not 0 < row["measured_qty"] <= row["requested_qty"]):
            raise ExecutionError("quote-arrival report has invalid measured depth")
    try:
        values = [Decimal(str(row["shortfall_bp"])) for row in rows]
    except (KeyError, ValueError) as exc:
        raise ExecutionError("quote-arrival report has invalid shortfall values") from exc
    if any(not value.is_finite() for value in values):
        raise ExecutionError("shortfall values must be finite")
    requested = sum(row["requested_qty"] for row in rows)
    measured = sum(row["measured_qty"] for row in rows)
    return {
        "benchmark_type": "SUBSEQUENT_EXECUTABLE_QUOTE",
        "fidelity": "SIMULATED_QUOTE",
        "broker_fill_observations": 0,
        "samples": len(rows),
        "mean_shortfall_bp": str(sum(values, Decimal(0)) / len(values)),
        "depth_coverage": measured / requested if requested else 0.0,
        "source_quote_sha256s": sorted({str(row["source_quote_sha256"]) for row in rows}),
    }


def marketable_limit(intent: OrderIntent, quotes: list[Quote], latency: timedelta,
                     price_band: tuple[Decimal, Decimal] | None = None) -> ExecResult:
    q = earliest_quote(quotes, intent.decision_utc, latency)
    if q is None:
        return ExecResult("MISSED", 0, None, "NO_QUOTE_AFTER_LATENCY")
    if intent.side == "BUY":
        price, size = round_to_tick(q.ask, intent.tick, against_buyer=True), q.ask_qty
        if intent.limit is not None and price > intent.limit:
            return ExecResult("MISSED", 0, None, "LIMIT_NOT_MARKETABLE")
    else:
        price, size = round_to_tick(q.bid, intent.tick, against_buyer=False), q.bid_qty
        if intent.limit is not None and price < intent.limit:
            return ExecResult("MISSED", 0, None, "LIMIT_NOT_MARKETABLE")
    if price_band is not None and not (price_band[0] <= price <= price_band[1]):
        return ExecResult("REJECTED", 0, None, "PRICE_BAND")
    filled = min(intent.quantity, size)
    if filled <= 0:
        return ExecResult("MISSED", 0, None, "NO_VISIBLE_SIZE")
    return ExecResult("FILLED" if filled == intent.quantity else "PARTIAL", filled, price,
                      "AT_TOUCH" if filled == intent.quantity else "VISIBLE_SIZE_CAP")


def passive_limit(intent: OrderIntent, trades: list[tuple[datetime, Decimal, int]],
                  latency: timedelta) -> ExecResult:
    """Conservative: only a unique, post-latency print strictly through the limit can fill.

    Without provider trade IDs, exact duplicate (UTC timestamp, price, quantity)
    tuples are collapsed to avoid granting duplicate feed prints extra queue volume.
    Distinct prints sharing that exact tuple may therefore be undercounted.
    """
    if intent.limit is None:
        raise ExecutionError("a passive order needs a limit")
    cutoff = _aware(intent.decision_utc) + latency
    remaining = intent.quantity
    cost = Decimal(0)
    unique_trades = []
    seen: set[tuple[datetime, Decimal, int]] = set()
    for ts, price, qty in trades:
        instant = _aware(ts)
        if not isinstance(price, Decimal) or not price.is_finite() or price <= 0:
            raise ExecutionError("trade prints need a positive finite Decimal price")
        if type(qty) is not int or qty <= 0:
            raise ExecutionError("trade prints need a positive integer quantity")
        identity = (instant, price, qty)
        if identity in seen:
            continue
        seen.add(identity)
        unique_trades.append((instant, price, qty))
    for instant, price, qty in sorted(unique_trades, key=lambda trade: trade[0]):
        if instant < cutoff or remaining == 0:
            continue
        through = price < intent.limit if intent.side == "BUY" else price > intent.limit
        if through:
            take = min(remaining, qty)
            remaining -= take
            cost += intent.limit * take  # filled at the limit, never at the better printed price
    filled = intent.quantity - remaining
    if filled == 0:
        return ExecResult("MISSED", 0, None, "NO_TRADE_THROUGH_LIMIT", queue_position_known=False)
    return ExecResult("FILLED" if remaining == 0 else "PARTIAL", filled, (cost / filled).quantize(Decimal("0.0001")),
                      "TRADED_THROUGH", queue_position_known=False)


def apply_adverse_selection(result: ExecResult, side: str, bps: Decimal) -> ExecResult:
    """Passive fills are worse on average: shift the average price against the trader by ``bps``."""
    if result.avg_price is None:
        return result
    sign = Decimal(1) if side == "BUY" else Decimal(-1)
    shifted = result.avg_price * (Decimal(1) + sign * bps / Decimal(10_000))
    return ExecResult(result.status, result.filled_qty, shifted, result.reason + "+ADVERSE", result.fidelity,
                      result.queue_position_known)


def slice_for_freeze(quantity: int, freeze_qty: int) -> list[int]:
    if quantity <= 0 or freeze_qty <= 0:
        raise ExecutionError("quantity and freeze quantity must be positive")
    full, rest = divmod(quantity, freeze_qty)
    return [freeze_qty] * full + ([rest] if rest else [])


# ---- FE-0060 effective-dated costs (rates are data, supplied by the owner) ----

@dataclass(frozen=True)
class CostRule:
    effective_from: date
    brokerage_bps: Decimal
    exchange_bps: Decimal
    stt_bps_buy: Decimal
    stt_bps_sell: Decimal
    stamp_bps_buy: Decimal
    gst_fraction: Decimal  # applied to brokerage + exchange charges
    sebi_bps: Decimal = Decimal("0")
    ipft_bps: Decimal = Decimal("0")
    clearing_bps: Decimal = Decimal("0")
    brokerage_min_inr: Decimal = Decimal("0")
    brokerage_max_inr: Decimal | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.effective_from, date) or isinstance(self.effective_from, datetime):
            raise ExecutionError("cost rule needs an effective calendar date")
        rates = (self.brokerage_bps, self.exchange_bps, self.stt_bps_buy, self.stt_bps_sell,
                 self.stamp_bps_buy, self.gst_fraction, self.sebi_bps, self.ipft_bps,
                 self.clearing_bps, self.brokerage_min_inr)
        if any(not isinstance(value, Decimal) or not value.is_finite() or value < 0 for value in rates):
            raise ExecutionError("cost rates and brokerage floor must be finite nonnegative Decimals")
        if self.gst_fraction > 1:
            raise ExecutionError("GST fraction cannot exceed 100 percent")
        if self.brokerage_max_inr is not None:
            if (not isinstance(self.brokerage_max_inr, Decimal) or not self.brokerage_max_inr.is_finite()
                    or self.brokerage_max_inr < 0 or self.brokerage_min_inr > self.brokerage_max_inr):
                raise ExecutionError("brokerage cap must be finite and at least the brokerage floor")


@dataclass(frozen=True)
class CostSchedule:
    rules: dict[str, tuple[CostRule, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.rules or any(not isinstance(segment, str) or not segment.strip() or not rules
                                 for segment, rules in self.rules.items()):
            raise ExecutionError("cost schedule needs nonempty named segment rules")
        for rules in self.rules.values():
            if len({rule.effective_from for rule in rules}) != len(rules):
                raise ExecutionError("cost schedule has duplicate effective dates")

    @property
    def sha256(self) -> str:
        """Content identity for the complete effective-dated table supplied by the owner."""
        rows = []
        for segment, rules in sorted(self.rules.items()):
            for rule in sorted(rules, key=lambda item: item.effective_from):
                rows.append({"segment": segment, "effective_from": rule.effective_from.isoformat(),
                             **{name: (format(getattr(rule, name), "f")
                                       if getattr(rule, name) is not None else None)
                                for name in ("brokerage_bps", "exchange_bps", "stt_bps_buy", "stt_bps_sell",
                                             "stamp_bps_buy", "gst_fraction", "sebi_bps", "ipft_bps",
                                             "clearing_bps", "brokerage_min_inr", "brokerage_max_inr")}})
        encoded = json.dumps(rows, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        return hashlib.sha256(encoded).hexdigest()

    def rule_for(self, segment: str, day: date) -> CostRule:
        eligible = [r for r in self.rules.get(segment, ()) if r.effective_from <= day]
        if not eligible:
            raise ExecutionError(f"no cost rule effective for {segment} on {day}")
        return max(eligible, key=lambda r: r.effective_from)

    def charges(self, segment: str, side: str, notional: Decimal, day: date) -> dict[str, Decimal | str]:
        if side not in {"BUY", "SELL"}:
            raise ExecutionError("cost side must be BUY or SELL")
        if not isinstance(notional, Decimal) or not notional.is_finite() or notional <= 0:
            raise ExecutionError("cost notional must be a positive finite Decimal")
        r = self.rule_for(segment, day)
        bps = Decimal(10_000)
        brokerage = notional * r.brokerage_bps / bps
        brokerage = max(brokerage, r.brokerage_min_inr)
        if r.brokerage_max_inr is not None:
            brokerage = min(brokerage, r.brokerage_max_inr)
        exchange = notional * r.exchange_bps / bps
        sebi = notional * r.sebi_bps / bps
        ipft = notional * r.ipft_bps / bps
        clearing = notional * r.clearing_bps / bps
        stt = notional * (r.stt_bps_buy if side == "BUY" else r.stt_bps_sell) / bps
        stamp = notional * r.stamp_bps_buy / bps if side == "BUY" else Decimal(0)
        gst = (brokerage + exchange + sebi + ipft + clearing) * r.gst_fraction
        total = brokerage + exchange + sebi + ipft + clearing + stt + stamp + gst
        parts = {"brokerage": brokerage, "exchange": exchange, "sebi": sebi, "ipft": ipft,
                 "clearing": clearing, "stt": stt, "stamp": stamp, "gst": gst}
        out: dict[str, Decimal | str] = {
            key: value.quantize(CENT, rounding=ROUND_HALF_EVEN) for key, value in parts.items()
        }
        out["total"] = total.quantize(CENT, rounding=ROUND_HALF_EVEN)  # rounded once from unrounded parts
        out["schedule_sha256"] = self.sha256
        return out


# ---- FE-0061 stress ----

def stress_net(gross_pnl: Decimal, costs: Decimal, slippage: Decimal,
               multipliers: tuple[Decimal, ...] = (Decimal(1), Decimal("1.5"), Decimal(2))) -> dict[Decimal, Decimal]:
    return {m: gross_pnl - m * (costs + slippage) for m in multipliers}


def positive_scenarios(stressed: dict[Decimal, Decimal]) -> list[Decimal]:
    return sorted(m for m, v in stressed.items() if v > 0)


# ---- FE-0062 options, FE-0064 FX ----

def multi_leg_executable(legs: list[tuple[Decimal | None, Decimal | None, int, int]]) -> tuple[bool, str]:
    """Every leg needs a two-sided, sized, uncrossed quote; one missing leg makes the whole structure non-executable."""
    for i, (bid, ask, bq, aq) in enumerate(legs):
        if bid is None or ask is None:
            return False, f"LEG_{i}_ONE_SIDED"
        if bid <= 0 or ask < bid:
            return False, f"LEG_{i}_INVALID"
        if bq <= 0 or aq <= 0:
            return False, f"LEG_{i}_NO_DEPTH"
    return True, "EXECUTABLE"


def margin_or_blocked(exchange_margin: Decimal | None) -> tuple[str, Decimal | None]:
    """Margin comes from the exchange/broker only; a theoretical figure is never substituted."""
    if exchange_margin is None:
        return "BLOCKED_NO_OFFICIAL_MARGIN", None
    return "OK", exchange_margin


def fx_execution_allowed() -> bool:
    """Simulation only: no global spot FX execution is implied anywhere."""
    return False


# ---- FE-0065 receipts with fidelity ----

FIDELITIES = frozenset({"SIMULATED_QUOTE", "SIMULATED_BAR", "OBSERVED_BROKER"})


class MixedFidelity(ExecutionError):
    pass


@dataclass(frozen=True)
class ExecutionReceipt:
    order_ref: str
    fidelity: str
    quantity: int
    price: Decimal

    def __post_init__(self) -> None:
        if self.fidelity not in FIDELITIES:
            raise ExecutionError("unknown fidelity")


def totals_by_fidelity(receipts: list[ExecutionReceipt]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for r in receipts:
        out[r.fidelity] = out.get(r.fidelity, Decimal(0)) + r.quantity * r.price
    return out


def combined_total(receipts: list[ExecutionReceipt]) -> Decimal:
    if len({r.fidelity for r in receipts}) > 1:
        raise MixedFidelity("fills of different fidelity are never aggregated as equivalent")
    return sum((r.quantity * r.price for r in receipts), Decimal(0))


# ---- FE-0066 fill uncertainty ----

def fill_bounds(side: str, quotes: list[Quote]) -> tuple[Decimal, Decimal, Decimal]:
    """(best, expected, worst) execution price over the quotes inside the latency uncertainty window."""
    if not quotes:
        raise ExecutionError("no quotes in the window")
    if side == "BUY":
        prices = [q.ask for q in quotes]
        return min(prices), sum(prices, Decimal(0)) / Decimal(len(prices)), max(prices)
    prices = [q.bid for q in quotes]
    return max(prices), sum(prices, Decimal(0)) / Decimal(len(prices)), min(prices)


# ---- FE-0067 post-trade identity ----

def identity_holds(*, cash_before: Decimal, cash_after: Decimal, position_before: int, position_after: int,
                   side: str, quantity: int, price: Decimal, charges: Decimal) -> bool:
    signed = quantity if side == "BUY" else -quantity
    cash_delta = -price * quantity - charges if side == "BUY" else price * quantity - charges
    return (position_after - position_before == signed and
            (cash_after - cash_before).quantize(CENT) == cash_delta.quantize(CENT))


# ---- FE-0068 outage is an explicit positive outcome ----

def execute_or_no_trade(feed_ok: bool, reasons: list[str], intent: OrderIntent, quotes: list[Quote],
                        latency: timedelta) -> ExecResult:
    if not feed_ok:
        return ExecResult("NO_TRADE", 0, None, "+".join(sorted(reasons)) or "FEED_NOT_OK")
    return marketable_limit(intent, quotes, latency)
