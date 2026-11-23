"""ETF quote/depth, capacity, trading-gap and daily-liquidity diagnostics."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date
from math import ceil
from statistics import median, stdev
from typing import Iterable

from .costs.india import DEFAULT as DEFAULT_COSTS, Segment, Side
from .etf_history import ETFPriceBar
from .forward_edge.market_events import EventKind, MarketEvent


@dataclass(frozen=True)
class DepthSweep:
    side: str
    requested_units: int
    filled_units: int
    residual_units: int
    vwap: float | None
    reference_mid: float
    impact_bps: float | None
    complete: bool


def sweep_depth(levels: Iterable[dict], *, units: int, side: str,
                reference_mid: float) -> DepthSweep:
    """Walk displayed price levels; never extrapolate beyond observed depth."""
    if units <= 0 or reference_mid <= 0 or side not in {"buy", "sell"}:
        raise ValueError("units, reference_mid and side are invalid")
    valid = []
    for row in levels:
        try:
            price, quantity = float(row["price"]), int(row["quantity"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(price) and price > 0 and quantity > 0:
            valid.append((price, quantity))
    ordered = sorted(valid, key=lambda row: row[0], reverse=(side == "sell"))
    left, notional, filled = units, 0.0, 0
    for price, quantity in ordered:
        take = min(left, quantity)
        notional += take * price
        filled += take
        left -= take
        if left == 0:
            break
    vwap = notional / filled if filled else None
    impact = ((vwap / reference_mid - 1) * 10_000 if side == "buy" else
              (1 - vwap / reference_mid) * 10_000) if vwap is not None else None
    return DepthSweep(side, units, filled, left, vwap, reference_mid, impact, left == 0)


def report(*, events: Iterable[MarketEvent], bars: Iterable[ETFPriceBar],
           intended_units: int, expected_sessions: Iterable[date] = (),
           expected_poll_seconds: int = 60, max_stale_seconds: int | None = None,
           costs=DEFAULT_COSTS) -> dict:
    """Summarize observable liquidity and modeled cost without treating missing depth as fillable."""
    if intended_units <= 0 or expected_poll_seconds <= 0:
        raise ValueError("size and feed timing limits must be positive")
    max_stale_seconds = max_stale_seconds or ceil(expected_poll_seconds * 1.5)
    if max_stale_seconds <= 0:
        raise ValueError("size and feed timing limits must be positive")
    quotes = sorted((event for event in events
                     if event.kind is EventKind.QUOTE and event.venue == "NSE"
                     and event.source == "KITE_CONNECT_READ_ONLY"),
                    key=lambda event: event.event_time)
    history = sorted(bars, key=lambda bar: bar.trade_date)
    spreads: list[float] = []
    halted = 0
    bid_depth_value: list[float] = []
    ask_depth_value: list[float] = []
    for event in quotes:
        payload = event.payload
        try:
            bid, ask = float(payload["bid"]), float(payload["ask"])
            mid = (bid + ask) / 2
            if bid > 0 and ask >= bid and mid > 0:
                spreads.append((ask - bid) / mid * 10_000)
            depth = payload.get("depth") or {}
            bid_levels = depth.get("buy") or []
            ask_levels = depth.get("sell") or []
            bid_depth_value.append(sum(float(x["price"]) * int(x["quantity"])
                                       for x in bid_levels))
            ask_depth_value.append(sum(float(x["price"]) * int(x["quantity"])
                                       for x in ask_levels))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
        status = str(payload.get("exchange_status", "")).upper()
        halted += status in {"HALTED", "SUSPENDED", "PAUSED", "CLOSED"}

    latest = quotes[-1] if quotes else None
    buy_sweep = sell_sweep = None
    buy_cost = sell_cost = None
    if latest and "depth" in latest.payload:
        depth = latest.payload["depth"]
        bid, ask = float(latest.payload["bid"]), float(latest.payload["ask"])
        mid = (bid + ask) / 2
        buy_sweep = asdict(sweep_depth(depth.get("sell", []), units=intended_units,
                                       side="buy", reference_mid=mid))
        sell_sweep = asdict(sweep_depth(depth.get("buy", []), units=intended_units,
                                        side="sell", reference_mid=mid))
        if buy_sweep["complete"]:
            buy_cost = costs.one_way(turnover=buy_sweep["vwap"] * intended_units,
                                     side=Side.BUY, segment=Segment.DELIVERY,
                                     include_impact=False)
        if sell_sweep["complete"]:
            sell_cost = costs.one_way(turnover=sell_sweep["vwap"] * intended_units,
                                      side=Side.SELL, segment=Segment.DELIVERY,
                                      include_impact=False)

    gaps = [(right.event_time - left.event_time).total_seconds()
            for left, right in zip(quotes, quotes[1:])]
    stale_intervals = sum(gap > max_stale_seconds for gap in gaps)
    turnover = [float(bar.turnover) for bar in history
                if bar.turnover is not None and bar.turnover > 0]
    returns: list[float] = []
    amihud: list[float] = []
    for prev, current in zip(history, history[1:]):
        if prev.close > 0 and current.close > 0:
            value = math.log(current.close / prev.close)
            returns.append(value)
            if current.turnover and current.turnover > 0:
                amihud.append(abs(value) / current.turnover)
    observed = {bar.trade_date for bar in history}
    expected = set(expected_sessions)
    missing_sessions = sorted(expected - observed)
    return {
        "quotes": len(quotes),
        "quoted_spread_median_bps": median(spreads) if spreads else None,
        "effective_spread_bps": None,
        "effective_spread_reason": "TRADE_SIDE_AND_SYNCHRONIZED_TRADE_PRINT_UNAVAILABLE",
        "bid_depth_value_median": median(bid_depth_value) if bid_depth_value else None,
        "ask_depth_value_median": median(ask_depth_value) if ask_depth_value else None,
        "stale_interval_count": stale_intervals,
        "stale_interval_rate": stale_intervals / len(gaps) if gaps else None,
        "max_quote_gap_seconds": max(gaps) if gaps else None,
        "halted_quote_fraction": halted / len(quotes) if quotes else None,
        "median_daily_traded_value": median(turnover) if turnover else None,
        "daily_volatility_annualized": stdev(returns) * math.sqrt(252) if len(returns) > 1 else None,
        "amihud_illiquidity_median": median(amihud) if amihud else None,
        "history_sessions": len(history),
        "missing_expected_sessions": [d.isoformat() for d in missing_sessions],
        "buy_depth_sweep": buy_sweep,
        "sell_depth_sweep": sell_sweep,
        "buy_cost_model": buy_cost,
        "sell_cost_model": sell_cost,
        "cost_model_source": "costs.india.DEFAULT",
        "intended_units": intended_units,
    }
