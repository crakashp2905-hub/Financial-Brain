"""Three external-script-inspired mechanisms, specified independently for Indian data.

No MQL5 or Pine source is copied. These are *research candidates*, not live signals,
strategy verdicts or broker instructions. Daily signals use completed bars only and
cannot be filled before the following session's open. Diagnostics are sealed to the
2016-2021 training period; ADR-0004 controls any later registered test.
"""
from __future__ import annotations

import math
import argparse
import json
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd

TRAIN_END = date(2021, 12, 31)
ROUND_TRIP_COST = 0.002  # training stress assumption, not a measured fill
MAX_QUOTE_AGE = timedelta(seconds=120)


def daily_signals(bars: pd.DataFrame, regimes: pd.Series, *, breakout_window: int = 55,
                  compression_window: int = 20, short_atr_window: int = 10,
                  long_atr_window: int = 60, compression_ratio: float = 0.65,
                  volume_window: int = 20, volume_multiple: float = 1.0) -> pd.DataFrame:
    """Signal at the completed daily close; `regimes` is shifted one session.

    `bars` has unique, ascending session dates and raw OHLCV for one symbol.
    `regimes` is indexed by exactly those sessions; only yesterday's regime can
    influence today's signal. This function deliberately computes no future return.
    """
    required = {"session", "open", "high", "low", "close", "volume"}
    if min(breakout_window, compression_window, short_atr_window, long_atr_window, volume_window) < 2:
        raise ValueError("windows must be at least two sessions")
    if short_atr_window >= long_atr_window or not 0 < compression_ratio < 1 or volume_multiple <= 0:
        raise ValueError("invalid compression or volume parameters")
    if not required.issubset(bars) or bars.empty:
        raise ValueError("complete OHLCV bars are required")
    x = bars.copy().reset_index(drop=True)
    x["session"] = pd.to_datetime(x["session"])
    if x["session"].isna().any() or not x["session"].is_monotonic_increasing or x["session"].duplicated().any():
        raise ValueError("sessions must be unique and ascending")
    if x["session"].dt.date.max() > TRAIN_END:
        raise ValueError("external-script exploration is restricted to training dates")
    if not regimes.index.equals(pd.Index(x["session"])):
        raise ValueError("regime dates must exactly match bar dates")
    if (x[["open", "high", "low", "close"]] <= 0).any().any() or (x["volume"] < 0).any():
        raise ValueError("prices must be positive and volume non-negative")
    if x[["open", "high", "low", "close", "volume"]].isna().any().any():
        raise ValueError("missing OHLCV is not a flat bar")
    if ((x["high"] < x[["open", "close", "low"]].max(axis=1)) |
            (x["low"] > x[["open", "close"]].min(axis=1))).any():
        raise ValueError("inconsistent OHLCV bar")

    # Yesterday's completed regime is used because a today-close regime could
    # itself consume today's breakout bar or be recomputed later.
    prior_regime = regimes.reset_index(drop=True).shift(1)
    high55 = x["high"].rolling(breakout_window, min_periods=breakout_window).max().shift(1)
    high20 = x["high"].rolling(compression_window, min_periods=compression_window).max().shift(1)
    previous_close = x["close"].shift(1)
    true_range = pd.concat([x["high"] - x["low"],
                            (x["high"] - previous_close).abs(),
                            (x["low"] - previous_close).abs()], axis=1).max(axis=1)
    short_atr = true_range.rolling(short_atr_window, min_periods=short_atr_window).mean().shift(1)
    long_atr = true_range.rolling(long_atr_window, min_periods=long_atr_window).mean().shift(1)
    prior_volume = x["volume"].rolling(volume_window, min_periods=volume_window).median().shift(1)
    market_ok = prior_regime.isin(("BULL", "TREND_UP", "UP"))
    base_breakout = x["close"] > high55
    compression = (short_atr / long_atr < compression_ratio) & (x["close"] > high20) & (x["volume"] > volume_multiple * prior_volume)
    return pd.DataFrame({"session": x["session"],
                         "donchian_baseline": base_breakout.fillna(False).astype(bool),
                         "regime_breakout": (base_breakout & market_ok).fillna(False).astype(bool),
                         "compression_breakout": compression.fillna(False).astype(bool),
                         "prior_regime": prior_regime})


def training_outcomes(bars: pd.DataFrame, signals: pd.DataFrame, *, hold_sessions: int = 10,
                      round_trip_cost: float = ROUND_TRIP_COST) -> dict:
    """Descriptive next-open to later-close returns, not a G1-G5 verdict.

    All selected entries with unavailable exits are counted as missing, never silently
    deleted from the denominator. Overlap and capital constraints are not modelled.
    """
    if hold_sessions < 1 or not 0 <= round_trip_cost < 1:
        raise ValueError("invalid hold or cost")
    if not pd.to_datetime(bars["session"]).reset_index(drop=True).equals(signals["session"].reset_index(drop=True)):
        raise ValueError("signal and price sessions do not align")
    if pd.to_datetime(bars["session"]).dt.date.max() > TRAIN_END:
        raise ValueError("training outcomes cannot inspect the holdout")
    entry = bars["open"].shift(-1).reset_index(drop=True)
    exit_price = bars["close"].shift(-(1 + hold_sessions)).reset_index(drop=True)
    if ((entry <= 0) & entry.notna()).any() or ((exit_price <= 0) & exit_price.notna()).any():
        raise ValueError("invalid executable price")
    net = exit_price / entry - 1 - round_trip_cost
    out = {}
    for name in ("donchian_baseline", "regime_breakout", "compression_breakout"):
        selected = signals[name].astype(bool).reset_index(drop=True)
        valid = net[selected & net.notna()]
        out[name] = {"selected": int(selected.sum()), "priced": len(valid),
                     "missing_exit": int((selected & net.isna()).sum()),
                     "sum_net_per_signal": float(valid.sum()) if len(valid) else None,
                     "mean_net": float(valid.mean()) if len(valid) else None,
                     "median_net": float(valid.median()) if len(valid) else None}
    skipped = (signals["donchian_baseline"].astype(bool) &
               ~signals["regime_breakout"].astype(bool)).reset_index(drop=True)
    out["regime_filter_counterfactual"] = {
        "skipped": int(skipped.sum()), "unpriced_skips": int((skipped & net.isna()).sum()),
        "avoided_losers": int((skipped & (net < 0)).sum()),
        "missed_winners": int((skipped & (net > 0)).sum()),
        "net_change_per_signal_sum": float(-net[skipped & net.notna()].sum())}
    out["status"] = "TRAINING_DESCRIPTION_ONLY_NOT_A_TRIAL"
    out["cost_assumption"] = round_trip_cost
    return out


def liquidity_no_trade(quote: dict, *, now: datetime, side: str, quantity: int,
                       max_spread_bps: float = 15, max_walk_bps: float = 25) -> dict:
    """Fail-closed intraday gate using fresh two-sided visible book depth.

    It estimates immediate book-walk for the requested size; it does *not* promise a
    fill or create a trading signal. The same gate can be applied to frozen baseline
    paper trades and scored with `research_program.filter_counterfactual`.
    """
    if now.tzinfo is None or quantity <= 0 or side not in ("BUY", "SELL") or max_spread_bps < 0 or max_walk_bps < 0:
        raise ValueError("timezone-aware now, positive quantity and BUY/SELL are required")
    stamp = quote.get("timestamp")
    try:
        seen = stamp if isinstance(stamp, datetime) else datetime.fromisoformat(str(stamp))
    except ValueError:
        seen = None
    if seen is None or seen.tzinfo is None or not timedelta(0) <= now - seen <= MAX_QUOTE_AGE:
        return {"allow": False, "reason": "STALE_OR_MISSING_QUOTE"}
    depth = quote.get("depth") or {}
    bids, asks = depth.get("buy") or [], depth.get("sell") or []
    if not bids or not asks:
        return {"allow": False, "reason": "NO_TWO_SIDED_BOOK"}
    bid, ask = float(bids[0].get("price") or 0), float(asks[0].get("price") or 0)
    if not all(math.isfinite(v) for v in (bid, ask)) or bid <= 0 or ask < bid:
        return {"allow": False, "reason": "INVALID_OR_CROSSED_BOOK"}
    mid = (bid + ask) / 2
    spread_bps = (ask - bid) / mid * 10_000
    levels = asks if side == "BUY" else bids
    left, paid = quantity, 0.0
    for level in levels:
        price, available = float(level.get("price") or 0), int(level.get("quantity") or 0)
        if not math.isfinite(price) or price <= 0 or available < 0:
            return {"allow": False, "reason": "INVALID_DEPTH"}
        take = min(left, available)
        paid += take * price
        left -= take
        if left == 0:
            break
    if left:
        return {"allow": False, "reason": "INSUFFICIENT_VISIBLE_DEPTH", "unfilled": left}
    avg = paid / quantity
    walk_bps = ((avg / mid - 1) if side == "BUY" else (1 - avg / mid)) * 10_000
    reason = ("WIDE_SPREAD" if spread_bps > max_spread_bps else
              "EXPENSIVE_BOOK_WALK" if walk_bps > max_walk_bps else "PASS")
    return {"allow": reason == "PASS", "reason": reason, "spread_bps": spread_bps,
            "walk_bps": walk_bps, "visible_fill_price": avg, "quote_at": seen.isoformat()}


def liquidity_counterfactual(trades: list[dict]) -> dict:
    """Compare a liquidity gate with every frozen baseline trade, including missed winners.

    Each row must be a *preselected* 2016-2021 baseline trade with its decision-time
    quote and eventual net P&L. The gate cannot pick a new trade or cherry-pick losers.
    """
    from .research_program import filter_counterfactual

    checked, reasons = [], Counter()
    for trade in trades:
        gate = liquidity_no_trade(trade.get("quote") or {}, now=trade["decision_at"],
                                  side=trade["side"], quantity=trade["quantity"])
        reasons[gate["reason"]] += 1
        checked.append({"trade_id": trade["trade_id"], "day": trade["day"],
                        "baseline_net_inr": trade["baseline_net_inr"], "keep": gate["allow"]})
    return {**filter_counterfactual(checked), "gate_reasons": dict(reasons)}


def training_scan(symbol: str, *, minute_db: Path = Path("data/minute_history.duckdb"),
                  research_db: Path = Path("data/financial_brain.duckdb")) -> dict:
    """Read-only sealed-training scan; no trial ledger, paper book, or live path."""
    if not symbol or not symbol.replace("-", "").isalnum():
        raise ValueError("one literal stock symbol is required")
    if not minute_db.exists() or not research_db.exists():
        raise FileNotFoundError("minute and research DuckDB archives are required")
    minute = duckdb.connect(str(minute_db), read_only=True)
    try:
        bars = minute.execute("""SELECT CAST(ts AT TIME ZONE 'Asia/Kolkata' AS DATE) AS session,
                    ARG_MIN(open, ts) AS open, MAX(high) AS high, MIN(low) AS low,
                    ARG_MAX(close, ts) AS close, SUM(volume) AS volume, COUNT(*) AS minutes
                FROM minute_bars WHERE tradingsymbol = ? AND ts >= '2016-01-01'
                    AND ts < '2022-01-01'
                GROUP BY 1 HAVING COUNT(*) >= 300 ORDER BY 1""", [symbol]).df()
    finally:
        minute.close()
    if len(bars) < 250:
        return {"symbol": symbol, "sessions": len(bars), "status": "INSUFFICIENT_TRAINING_BARS"}
    research = duckdb.connect(str(research_db), read_only=True)
    try:
        index = research.execute("""SELECT business_date AS session, close_level
            FROM index_levels_canonical WHERE index_name = 'Nifty 50' AND variant = 'PRICE'
                AND business_date >= '2015-01-01' AND business_date < '2022-01-01'
            ORDER BY business_date""").df()
    finally:
        research.close()
    if index.empty:
        return {"symbol": symbol, "sessions": len(bars), "status": "MISSING_MARKET_REGIME_DATA"}
    index["session"] = pd.to_datetime(index["session"])
    index = index.set_index("session")["close_level"]
    market_ok = (index > index.rolling(200, min_periods=200).mean()).map({True: "BULL", False: "SIDEWAYS"})
    regime = market_ok.reindex(pd.to_datetime(bars["session"]), method="ffill")
    if regime.isna().any():
        return {"symbol": symbol, "sessions": len(bars), "status": "MISSING_MARKET_REGIME_DATA"}
    signals = daily_signals(bars, regime)
    result = training_outcomes(bars, signals)
    return {"symbol": symbol, "sessions": len(bars), "date_first": str(bars["session"].iloc[0].date()),
            "date_last": str(bars["session"].iloc[-1].date()), "results": result,
            "caveat": "Raw minute prices may include corporate-action discontinuities; no PIT survivorship audit, "
                      "fill calibration or overlap-adjusted significance is included."}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only training diagnostic for three script-inspired mechanisms")
    parser.add_argument("--symbol", required=True, help="one NSE stock symbol in the minute archive")
    parser.add_argument("--minute-db", type=Path, default=Path("data/minute_history.duckdb"))
    parser.add_argument("--research-db", type=Path, default=Path("data/financial_brain.duckdb"))
    args = parser.parse_args(argv)
    print(json.dumps(training_scan(args.symbol, minute_db=args.minute_db, research_db=args.research_db), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
