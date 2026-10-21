"""Market-hours stock and option scenarios with deduplicated owner notifications.

This process has no broker order path. Historical scanner returns and a bootstrap distribution
do not establish a tradeable edge: unconfirmed opportunities remain WATCH, including options.
Quotes must be live and two-sided before an option contract is named.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from datetime import date, datetime, time as clock_time, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import duckdb
import pandas as pd

from .. import alerts as AL
from ..forecasting.distribution import ForecastError
from ..forecasting.null import Climatology
from ..providers import kite_data as KD

IST = timezone(timedelta(hours=5, minutes=30))
SNAPSHOT = Path("data/terminal/opportunities.json")
FORECAST_LOG = Path("data/forecasts/watch.jsonl")
MARKET_OPEN, MARKET_CLOSE = clock_time(9, 15), clock_time(15, 30)
POLL_SECONDS = 60
MAX_STOCKS = 5
WATCH_NET = 0.01
MAX_QUOTE_AGE = timedelta(seconds=120)


def _history(con, symbol: str, as_of: date) -> list[float]:
    rows = con.execute("""SELECT close_adj FROM adjusted_prices
                          WHERE ticker = ? AND business_date <= ? AND close_adj > 0
                          ORDER BY business_date DESC LIMIT 260""", [symbol, as_of]).fetchall()
    return [float(r[0]) for r in reversed(rows)]


def stock_distribution(history: list[float], spot: float, symbol: str, as_of: date,
                       horizon: int = 20):
    """Bootstrap observed returns, rebased to a fresh spot; this is a baseline, not alpha."""
    if not math.isfinite(spot) or spot <= 0 or not history or history[-1] <= 0:
        raise ForecastError("a positive live spot and historical anchor are required")
    scaled = [p * spot / history[-1] for p in history]
    return Climatology().forecast(instrument=symbol, as_of=as_of, prices=scaled,
                                  horizon=horizon, n_paths=1000)


def option_scenario(dist, *, symbol: str, expiry: str, strike: float, kind: str,
                    ask: float, bid: float, lot: int) -> dict:
    """Long-option expiry payoffs at actual quoted ask. Fee/tax and early sale are excluded."""
    if kind not in ("CE", "PE") or min(strike, ask, bid, lot) <= 0 or ask < bid:
        raise ValueError("a positive two-sided option quote and valid contract are required")
    terminal = dist.terminal()
    payoffs = [max(p - strike, 0.0) if kind == "CE" else max(strike - p, 0.0) for p in terminal]
    net = [(p - ask) * lot for p in payoffs]
    return {"contract": f"{symbol} {expiry} {strike:g} {kind}", "underlying": symbol,
            "expiry": expiry, "strike": strike, "kind": kind, "ask": ask, "bid": bid, "lot": lot,
            "break_even": strike + ask if kind == "CE" else strike - ask,
            "p_profit_before_fees": sum(v > 0 for v in net) / len(net),
            "mean_payoff_before_fees_inr": sum(net) / len(net),
            "max_premium_loss_inr": ask * lot,
            "model": dist.model, "evidence": "scenario only; no validated options edge"}


def _option_contracts(instruments: list[dict], symbol: str, spot: float, today: date) -> list[dict]:
    options = [r for r in instruments if r.get("name") == symbol
               and r.get("instrument_type") in ("CE", "PE")
               and str(r.get("expiry", "")) > today.isoformat()]
    if not options:
        return []
    expiry = min(str(r["expiry"]) for r in options)
    near = [r for r in options if str(r["expiry"]) == expiry]
    return [min((r for r in near if r["instrument_type"] == kind),
                key=lambda r: abs(float(r["strike"]) - spot), default=None)
            for kind in ("CE", "PE") if any(r["instrument_type"] == kind for r in near)]


def _two_sided(quote: dict) -> tuple[float, float] | None:
    depth = quote.get("depth") or {}
    buy, sell = depth.get("buy") or [], depth.get("sell") or []
    if not buy or not sell:
        return None
    bid, ask = float(buy[0].get("price") or 0), float(sell[0].get("price") or 0)
    return (bid, ask) if bid > 0 and ask >= bid else None


def _fresh(quote: dict, now: datetime) -> bool:
    """A present LTP or two-sided book is insufficient if the quote timestamp is stale."""
    stamp = quote.get("timestamp")
    if not stamp:
        return False
    try:
        seen = stamp if isinstance(stamp, datetime) else datetime.fromisoformat(str(stamp))
    except ValueError:
        return False
    seen = seen.replace(tzinfo=IST) if seen.tzinfo is None else seen.astimezone(IST)
    return timedelta(0) <= now - seen <= MAX_QUOTE_AGE


@lru_cache(maxsize=1)
def _instruments() -> list[dict]:
    """The watcher lives for one session; its instrument list need not be fetched each minute."""
    return KD.instruments("NFO")


def _snapshot(path: Path, today: date) -> list[dict]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("opportunity snapshot is not a list")
    # A prior close is expected in the morning. An older board is not a live signal.
    valid = [r for r in rows if 0 < (today - date.fromisoformat(r["timestamp"])).days <= 4]
    return valid[:MAX_STOCKS]


def _holding_exits(research, today: date) -> list[dict]:
    from ..portfolio import my_book as MB
    if not MB.PATH.exists():
        return []
    book_con = duckdb.connect(str(MB.PATH), read_only=True)
    try:
        book = MB.latest(book_con)
    finally:
        book_con.close()
    if book.empty:
        return []
    if (today - pd.Timestamp(book["day"].max()).date()).days > 4:
        return []
    as_of = research.execute("SELECT MAX(business_date) FROM features").fetchone()[0]
    return MB.analyse(research, book, as_of)["holdings"]


def build_alerts(opportunities: list[dict], histories: dict[str, list[float]],
                 quotes: dict[str, dict], instruments: list[dict], holdings: list[dict],
                 *, today: date, now: datetime | None = None) -> tuple[list[dict], list[dict]]:
    """Pure decision step. Every alert contains its source date and evidentiary limit."""
    alerts, observations = [], []
    now = now or datetime.now(IST)
    held = {r["symbol"]: r for r in holdings}
    for symbol, r in held.items():
        q = quotes.get(f"NSE:{symbol}") or {}
        if not _fresh(q, now):
            continue
        spot = float(q.get("last_price") or 0)
        stop = float(r.get("stop") or 0)
        if spot > 0 and stop > 0 and spot <= stop:
            alerts.append({"key": f"sell-stop:{today}:{symbol}",
                           "title": f"SELL REVIEW: {symbol} crossed its recorded stop",
                           "body": f"Live Rs {spot:,.2f}, stop Rs {stop:,.2f}. Check holding and order details before acting. No order was sent."})
    for row in opportunities:
        symbol = row["ticker"]
        q = quotes.get(f"NSE:{symbol}") or {}
        if not _fresh(q, now):
            continue
        spot = float(q.get("last_price") or 0)
        if spot <= 0 or symbol not in histories:
            continue
        as_of = date.fromisoformat(row["timestamp"])
        try:
            dist = stock_distribution(histories[symbol], spot, symbol, as_of,
                                      horizon=min(int(row["horizon"]), 60))
        except ForecastError:
            continue
        net = float(row.get("alpha", {}).get("net") or 0)
        baseline = {"kind": "stock", "symbol": symbol, "day": today.isoformat(), "as_of": str(as_of),
                    "spot": spot, "horizon": dist.horizon, "p_up_baseline": dist.probability_above(spot),
                    "p10": dist.quantiles((0.1,))["p10"], "p90": dist.quantiles((0.9,))["p90"],
                    "model": dist.model, "scanner_net_in_sample": net,
                    "evidence": row.get("evidence", {}).get("status", "unknown")}
        observations.append(baseline)
        if net >= WATCH_NET:
            alerts.append({"key": f"watch-buy:{today}:{symbol}",
                           "title": f"WATCH BUY: {symbol} (unconfirmed)",
                           "body": (f"Live Rs {spot:,.2f}; {dist.horizon}-session baseline P(up) "
                                    f"{baseline['p_up_baseline']:.0%}. Historical model net {net:+.2%}; "
                                    f"evidence {baseline['evidence']}. Review in terminal. No validated buy signal.")})
        contracts = _option_contracts(instruments, symbol, spot, today)
        for c in contracts:
            key = f"NFO:{c['tradingsymbol']}"
            option_quote = quotes.get(key) or {}
            if not _fresh(option_quote, now):
                continue
            two = _two_sided(option_quote)
            if not two:
                continue
            bid, ask = two
            sessions = max(1, min(60, len(pd.bdate_range(today, date.fromisoformat(str(c["expiry"])))) - 1))
            opt_dist = stock_distribution(histories[symbol], spot, symbol, as_of, sessions)
            option = option_scenario(opt_dist, symbol=symbol, expiry=str(c["expiry"]),
                                     strike=float(c["strike"]), kind=c["instrument_type"],
                                     ask=ask, bid=bid, lot=int(float(c.get("lot_size") or 0)))
            option.update(record_type="option", day=today.isoformat(), as_of=str(as_of))
            observations.append(option)
            alerts.append({"key": f"watch-option:{today}:{c['tradingsymbol']}",
                           "title": f"OPTION WATCH: {option['contract']}",
                           "body": (f"Ask Rs {ask:,.2f}; expiry break-even Rs {option['break_even']:,.2f}; "
                                    f"baseline P(profit before fees) {option['p_profit_before_fees']:.0%}. "
                                    "Scenario only; no validated options edge or order.")})
    return alerts, observations


def once(*, now: datetime | None = None, dry_run: bool = False,
         snapshot: Path = SNAPSHOT) -> dict:
    """One market-hours poll; quotes and research database are read-only."""
    now = now or datetime.now(IST)
    today = now.date()
    if now.weekday() >= 5 or not MARKET_OPEN <= now.time() < MARKET_CLOSE:
        return {"status": "outside_market_hours", "alerts": 0}
    opportunities = _snapshot(snapshot, today)
    if not opportunities:
        return {"status": "no_fresh_opportunities", "alerts": 0}
    research = duckdb.connect("data/financial_brain.duckdb", read_only=True)
    try:
        symbols = [r["ticker"] for r in opportunities]
        histories = {s: _history(research, s, date.fromisoformat(opportunities[0]["timestamp"]))
                     for s in symbols}
        holdings = _holding_exits(research, today)
    finally:
        research.close()
    instruments = _instruments()
    holding_symbols = {r["symbol"] for r in holdings}
    spots = KD.quote([f"NSE:{s}" for s in sorted(set(symbols) | holding_symbols)], "full")
    contract_symbols = []
    for s in symbols:
        quote = spots.get(f"NSE:{s}") or {}
        spot = float(quote.get("last_price") or 0)
        if spot > 0 and _fresh(quote, now):
            contract_symbols.extend(f"NFO:{c['tradingsymbol']}" for c in _option_contracts(instruments, s, spot, today))
    option_quotes = {}
    for start in range(0, len(contract_symbols), KD.MAX_QUOTE_SYMBOLS):
        option_quotes.update(KD.quote(contract_symbols[start:start + KD.MAX_QUOTE_SYMBOLS], "full"))
    alerts, observations = build_alerts(opportunities, histories, {**spots, **option_quotes},
                                         instruments, holdings, today=today, now=now)
    if dry_run:
        return {"status": "dry_run", "alerts": alerts, "observations": observations}
    FORECAST_LOG.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    if FORECAST_LOG.exists():
        for line in FORECAST_LOG.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                seen.add((row.get("day"), row.get("symbol", row.get("contract"))))
            except ValueError:
                continue
    with FORECAST_LOG.open("a", encoding="utf-8") as out:
        for r in observations:
            identity = (r["day"], r.get("symbol", r.get("contract")))
            if identity not in seen:
                out.write(json.dumps({"recorded_at": now.isoformat(), **r}, default=str) + "\n")
                seen.add(identity)
    local = AL.send(alerts)
    mobile = AL.send(alerts, notify=AL.telegram, path=AL.TELEGRAM_SENT) if AL.telegram_configured() else []
    return {"status": "ok", "alerts": len(alerts), "desktop_sent": len(local), "telegram_sent": len(mobile)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only stock and option watch alerts during market hours")
    parser.add_argument("--once", action="store_true", help="poll once and exit")
    parser.add_argument("--dry-run", action="store_true", help="show scenarios without sending messages")
    parser.add_argument("--poll-seconds", type=int, default=POLL_SECONDS)
    args = parser.parse_args(argv)
    if args.poll_seconds < 30:
        parser.error("poll interval must be at least 30 seconds")
    while True:
        try:
            result = once(dry_run=args.dry_run)
            print(json.dumps(result, default=str), flush=True)
        except (OSError, ValueError, duckdb.Error, KD.KiteDataError, KD.KiteNotConfigured) as exc:
            print(f"watch unavailable: {type(exc).__name__}: {str(exc)[:120]}", flush=True)
            if args.once:
                return 1
        if args.once or datetime.now(IST).time() >= MARKET_CLOSE:
            return 0
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
