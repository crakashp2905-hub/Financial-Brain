"""Run the intraday paper engine through a trading day - live from Kite quotes, or as a replay of a past day's
minute candles - and keep the evidence.

    live     polls Kite's read-only quote endpoint every ``POLL_SECONDS`` from 09:15 to 15:30 IST, sets the
             opening range from the quote's own day high and low at 09:30, feeds every polled price to the
             engine, flattens at 15:15 and writes the day's report. Exits on its own on a holiday (no trade
             printed today by 09:25) and if the Kite session is missing or expires.
    replay   the same engine fed a past day's exact one-minute candles (open, then the bar's extremes in
             the order its direction implies, then the close). Labelled ``replay`` everywhere: it uses rules
             fixed in advance, but it is not the same evidence as a decision taken before the price existed.

Everything is written to ``data/paper_live.duckdb`` - its own file, so a day-long run never locks the research
database - as each trade opens and closes, so a crash loses nothing. There is no order code here and no path
to a broker: the only Kite calls are quotes and candles.
"""
from __future__ import annotations

import json
import logging
import time as _time
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from . import board as BD
from . import intraday as I
from ..costs.calculator import bucket_for

IST = timezone(timedelta(hours=5, minutes=30))
POLL_SECONDS = 20
LEDGER = Path("data") / "paper_live.duckdb"
REPORTS = Path("data") / "paper_reports"
UNIVERSE_SIZE = 200
OPEN, CLOSE = time(9, 15), time(15, 30)
HOLIDAY_CHECK = time(9, 25)

#: What the pre-registered backtest said each strategy should earn, net, per trade (bp of notional). Live and
#: replay results are compared to these; a strategy is not "learned" from noise, it is checked against them.
BACKTEST_NET_BP = {"ORB": -18.0, "GAP": None}

log = logging.getLogger("paper_intraday")

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    trade_id VARCHAR, mode VARCHAR, version VARCHAR, day VARCHAR, strategy VARCHAR, symbol VARCHAR, side VARCHAR,
    qty INTEGER, entry_ts VARCHAR, entry_px DOUBLE, stop DOUBLE, target DOUBLE, exit_ts VARCHAR, exit_px DOUBLE,
    exit_reason VARCHAR, gross_pnl DOUBLE, costs DOUBLE, net_pnl DOUBLE, r_multiple DOUBLE, bucket VARCHAR,
    PRIMARY KEY (trade_id, mode));
CREATE TABLE IF NOT EXISTS runs (day VARCHAR, mode VARCHAR, version VARCHAR, started VARCHAR, finished VARCHAR,
    status VARCHAR, symbols INTEGER, polls INTEGER, note VARCHAR, PRIMARY KEY (day, mode));
"""


EXTRA_COLUMNS = (("entry_decision_px", "DOUBLE"), ("exit_decision_px", "DOUBLE"), ("spread_bp", "DOUBLE"),
                 ("shortfall_bp", "DOUBLE"))


def ledger(path: Path = LEDGER):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(SCHEMA)
    for name, typ in EXTRA_COLUMNS:                     # added after the first live day; old rows read NULL
        con.execute(f"ALTER TABLE trades ADD COLUMN IF NOT EXISTS {name} {typ}")
    return con


def save(con, t: I.Trade, mode: str) -> None:
    con.execute("""INSERT OR REPLACE INTO trades (trade_id, mode, version, day, strategy, symbol, side, qty, entry_ts,
                   entry_px, stop, target, exit_ts, exit_px, exit_reason, gross_pnl, costs, net_pnl, r_multiple, bucket,
                   entry_decision_px, exit_decision_px, spread_bp, shortfall_bp)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [t.trade_id, mode, t.version, t.day, t.strategy, t.symbol, t.side, t.qty, t.entry_ts, t.entry_px,
                 t.stop, t.target, t.exit_ts, t.exit_px, t.exit_reason, t.gross_pnl, t.costs, t.net_pnl,
                 t.r_multiple, t.bucket, t.entry_decision_px, t.exit_decision_px, t.spread_bp, t.shortfall_bp])


def record_run(con, day: str, mode: str, **kw) -> None:
    con.execute("""INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?)""",
                [day, mode, I.VERSION, kw.get("started"), kw.get("finished"), kw.get("status"),
                 kw.get("symbols", 0), kw.get("polls", 0), kw.get("note")])


def daily_view():
    """Every scanner's opportunities at the latest feature date, reduced to each name's best, from the research
    database read briefly. Returns (board frame, as_of, research connection, opportunities); Nones if it cannot
    be read - the board is then skipped, not faked."""
    from ..opportunity import scanners as S
    try:
        rcon = duckdb.connect("data/financial_brain.duckdb", read_only=True)
    except Exception as e:                              # noqa: BLE001 - a locked research DB skips the board
        log.warning("daily view unavailable: %s", str(e)[:120])
        return None, None, None
    try:
        asof = rcon.execute("SELECT MAX(business_date) FROM features").fetchone()[0]
        cs = S.cross_section(rcon, asof)
        events = {r[0] for r in rcon.execute("""
            SELECT DISTINCT l.lineage FROM announcements a JOIN security_lineage l ON l.isin = a.isin
            WHERE a.business_date BETWEEN ?::DATE - INTERVAL 1 DAY AND ? AND a.materiality <> 'LOW'""",
                                             [asof, asof]).fetchall()}
        opps = S.scan(cs, asof, events=events)
        best = S.best_per_name(opps)
        frame = pd.DataFrame({"ticker": best["ticker"], "z": best["features"].map(lambda f: f.get("z", 0.0)),
                              "alpha": best["expected_return"], "cost": best["cost"], "net": best["expected_net"],
                              "vol_60": best["features"].map(lambda f: f.get("vol_60")), "adv20": best["liquidity"],
                              "type": best["type"], "horizon": best["horizon"], "evidence": best["evidence"]})
        frame.attrs["opportunities"] = opps
        return frame, asof, rcon
    except Exception as e:                              # noqa: BLE001
        log.warning("daily view failed: %s", str(e)[:160])
        rcon.close()
        return None, None, None


def _has_board(con) -> bool:
    return bool(con.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'board'").fetchone()[0])


def post_board(con, day: str, mode: str, quotes: dict, daily, asof) -> str | None:
    if daily is None:
        return None
    try:
        b = BD.build(quotes, daily, features_as_of=asof)
    except BD.BoardError as e:
        log.warning("no board: %s", e)
        return None
    BD.save(con, b, day, mode)
    REPORTS.mkdir(parents=True, exist_ok=True)
    text = BD.render(b, day)
    (REPORTS / f"{day}-board-{mode}.md").write_text(text, encoding="utf-8")
    return text


def universe(n: int = UNIVERSE_SIZE) -> list[dict]:
    """The n most liquid NSE names by recent turnover, from the research database, read-only and briefly."""
    from ..ingest import kite_minute as KM
    con = duckdb.connect("data/financial_brain.duckdb", read_only=True)
    try:
        return KM.liquid_universe(con, n)
    finally:
        con.close()


# --------------------------------------------------------------------------- live
LATE_START = time(9, 32)


def opening_from_candles(symbols, day: date, *, candles_fn, token_fn) -> dict[str, tuple]:
    """The exact 09:15-09:29 open, high and low from minute candles - for a run that starts after 09:30, when the
    quote's day high and low already include later trading and would be the wrong range."""
    out = {}
    for sym in symbols:
        tok = token_fn(sym)
        if tok is None:
            continue
        bars = candles_fn(tok, datetime.combine(day, time(9, 15)), datetime.combine(day, time(9, 29)), interval="minute")
        rng = [b for b in bars if b["ts"][11:16] < "09:30"]
        if len(rng) >= 10:
            out[sym] = (float(rng[0]["open"]), max(float(b["high"]) for b in rng), min(float(b["low"]) for b in rng))
    return out


def run_live(*, n: int = UNIVERSE_SIZE, poll: int = POLL_SECONDS, quote_fn=None, now_fn=None,
             sleep_fn=_time.sleep, path: Path = LEDGER, max_failures: int = 90, candles_fn=None,
             token_fn=None, daily_fn=None) -> dict:
    from ..providers import kite_data as kd
    quote_fn = quote_fn or kd.quote
    now_fn = now_fn or (lambda: datetime.now(IST))
    now = now_fn()
    day = now.date().isoformat()
    if now.time() > CLOSE or now.weekday() >= 5:
        return {"day": day, "status": "not_a_session", "why": "after the close or a weekend: nothing recorded"}
    con = ledger(path)
    uni = universe(n)
    syms = [u["tradingsymbol"] for u in uni]
    buckets = {u["tradingsymbol"]: bucket_for(u.get("adv")) for u in uni}
    eng = I.DayEngine(day)
    started = now.isoformat()
    daily, asof, rcon = daily_view() if daily_fn is None else daily_fn()
    if rcon is not None:
        from ..opportunity import scanners as S
        try:
            BD.resolve_horizons(con, rcon)
            S.resolve(con, rcon)
        finally:
            rcon.close()
    if daily is not None and daily.attrs.get("opportunities"):
        from ..opportunity import scanners as S
        S.save(con, daily.attrs["opportunities"], day)
    record_run(con, day, "live", started=started, status="running", symbols=len(syms))
    polls = failures = 0
    seen_today = False
    last: dict[str, float] = {}
    opened = False
    while True:
        now = now_fn()
        clock = now.time()
        if clock > CLOSE:
            break
        if clock < OPEN:
            sleep_fn(min(60, (datetime.combine(now.date(), OPEN, IST) - now).total_seconds()))
            continue
        try:
            q = {}
            for i in range(0, len(syms), 450):
                q.update(quote_fn([f"NSE:{s}" for s in syms[i:i + 450]], "full"))   # full: includes the order book
            failures = 0
        except Exception as e:                          # noqa: BLE001 - a bad poll is not the end of the day
            failures += 1
            log.warning("quote failed (%s/%s): %s", failures, max_failures, str(e)[:120])
            if failures >= max_failures:
                record_run(con, day, "live", started=started, finished=now.isoformat(), status="aborted",
                           symbols=len(syms), polls=polls, note=f"quotes failing: {str(e)[:120]}")
                return {"day": day, "status": "aborted", "why": str(e)[:200]}
            sleep_fn(poll)
            continue
        polls += 1
        for key, v in q.items():
            sym = key.split(":", 1)[-1]
            ltt = str(v.get("last_trade_time") or "")
            if ltt.startswith(day):
                seen_today = True
            lp = v.get("last_price")
            o = v.get("ohlc") or {}
            if lp:
                last[sym] = float(lp)
            if sym not in eng.symbols and o.get("close"):
                eng.add_symbol(sym, float(o["close"]), buckets.get(sym, "large"))
        if clock >= HOLIDAY_CHECK and not seen_today:
            record_run(con, day, "live", started=started, finished=now.isoformat(), status="closed",
                       symbols=len(syms), polls=polls, note="no trade printed today: holiday or no session")
            return {"day": day, "status": "closed"}
        if not opened and clock >= I.OR_END:
            if clock > LATE_START:
                # Started late (the Kite login came after 09:30): take the range from the exact candles.
                if candles_fn is None:
                    candles_fn = kd.candles
                if token_fn is None:
                    inst = kd.instruments("NSE")
                    token_fn = lambda s: kd.token_for(s, inst)          # noqa: E731
                exact = opening_from_candles(list(eng.symbols), now.date(), candles_fn=candles_fn, token_fn=token_fn)
                for sym, (op, hi, lo) in exact.items():
                    try:
                        eng.set_opening(sym, open_px=op, or_hi=hi, or_lo=lo)
                    except I.EngineError:
                        pass
                log.info("late start at %s: opening range from candles for %d names", clock, len(exact))
            else:
                for key, v in q.items():
                    sym = key.split(":", 1)[-1]
                    o = v.get("ohlc") or {}
                    if sym in eng.symbols and o.get("open") and o.get("high") and o.get("low"):
                        try:
                            eng.set_opening(sym, open_px=float(o["open"]), or_hi=float(o["high"]), or_lo=float(o["low"]))
                        except I.EngineError:
                            pass
            opened = True
            bq = {}
            for key, v in q.items():
                sym = key.split(":", 1)[-1]
                o = v.get("ohlc") or {}
                s_ = eng.symbols.get(sym)
                if v.get("last_price") and s_ is not None and s_.open is not None:
                    bq[sym] = {"price": float(v["last_price"]), "open": s_.open, "high": s_.or_hi, "low": s_.or_lo,
                               "prev_close": s_.prev_close}
            post_board(con, day, "live", bq, daily, asof)
            continue                                    # first fill no earlier than the next poll
        if opened:
            book = {}
            for key, v in q.items():
                dep = v.get("depth") or {}
                b, a = (dep.get("buy") or [{}])[0].get("price"), (dep.get("sell") or [{}])[0].get("price")
                if b and a:
                    book[key.split(":", 1)[-1]] = (float(b), float(a))
            for sym, px in last.items():
                bid, ask = book.get(sym, (None, None))
                for t in eng.on_price(sym, now.replace(tzinfo=None), px, bid=bid, ask=ask):
                    save(con, t, "live")
            if clock >= I.TIME_EXIT and not eng.closed_for_day:
                for t in eng.end_of_day(now.replace(tzinfo=None), last):
                    save(con, t, "live")
        sleep_fn(poll)
    if not eng.closed_for_day:
        for t in eng.end_of_day(now_fn().replace(tzinfo=None), last):
            save(con, t, "live")
    record_run(con, day, "live", started=started, finished=now_fn().isoformat(), status="complete",
               symbols=len(syms), polls=polls, note=json.dumps({"skipped": len(eng.skipped)}))
    if _has_board(con):
        BD.resolve_close(con, day, "live", last)
    out = {"day": day, "status": "complete", "summary": eng.summary(), "polls": polls}
    write_report(con, day, "live", eng)
    return out


# --------------------------------------------------------------------------- replay
def path_of_bar(o, h, l, c):
    """The order a bar's extremes are fed: an up bar is taken to have made its low first."""
    return (o, l, h, c) if c >= o else (o, h, l, c)


def run_replay(day: date, *, n: int = UNIVERSE_SIZE, candles_fn=None, token_fn=None, path: Path = LEDGER,
               daily_fn=None) -> dict:
    from ..providers import kite_data as kd
    candles_fn = candles_fn or kd.candles
    inst = kd.instruments("NSE") if token_fn is None else None
    token_fn = token_fn or (lambda s: kd.token_for(s, inst))
    con = ledger(path)
    uni = universe(n)
    eng = I.DayEngine(day.isoformat())
    stream = []
    used = 0
    for u in uni:
        sym = u["tradingsymbol"]
        tok = token_fn(sym)
        if tok is None:
            continue
        daily = candles_fn(tok, day - timedelta(days=10), day, interval="day")
        prev = [r for r in daily if r["ts"][:10] < day.isoformat()]
        bars = candles_fn(tok, datetime.combine(day, time(9, 15)), datetime.combine(day, time(15, 30)), interval="minute")
        if not prev or len(bars) < 100:
            continue
        eng.add_symbol(sym, float(prev[-1]["close"]), bucket_for(u.get("adv")))
        rng = [b for b in bars if b["ts"][11:16] < "09:30"]
        if len(rng) < 10:
            continue
        eng.set_opening(sym, open_px=float(rng[0]["open"]), or_hi=max(float(b["high"]) for b in rng),
                        or_lo=min(float(b["low"]) for b in rng))
        used += 1
        for b in bars:
            if b["ts"][11:16] < "09:30":
                continue
            t0 = datetime.fromisoformat(b["ts"][:19])
            for k, px in enumerate(path_of_bar(b["open"], b["high"], b["low"], b["close"])):
                stream.append((t0 + timedelta(seconds=15 * k), sym, float(px)))
    stream.sort(key=lambda x: (x[0], x[1]))
    daily, asof, rcon = daily_view() if daily_fn is None else daily_fn()
    if rcon is not None:
        rcon.close()
    bq = {sym: {"price": None, "open": s_.open, "high": s_.or_hi, "low": s_.or_lo, "prev_close": s_.prev_close}
          for sym, s_ in eng.symbols.items() if s_.open is not None}
    first = {}
    for ts, sym, px in stream:
        if sym not in first:
            first[sym] = px                             # the first price at or after 09:30
    for sym in bq:
        bq[sym]["price"] = first.get(sym)
    post_board(con, day.isoformat(), "replay", {k: v for k, v in bq.items() if v["price"]}, daily, asof)
    last = {}
    for ts, sym, px in stream:
        last[sym] = px
        for t in eng.on_price(sym, ts, px):
            save(con, t, "replay")
    for t in eng.end_of_day(datetime.combine(day, time(15, 20)), last):
        save(con, t, "replay")
    if _has_board(con):
        BD.resolve_close(con, day.isoformat(), "replay", last)
    record_run(con, day.isoformat(), "replay", started=datetime.now(IST).isoformat(),
               finished=datetime.now(IST).isoformat(), status="complete", symbols=used, polls=len(stream))
    write_report(con, day.isoformat(), "replay", eng)
    return {"day": day.isoformat(), "status": "complete", "symbols": used, "summary": eng.summary()}


# --------------------------------------------------------------------------- evidence
def evidence(con, mode: str | None = None) -> list[dict]:
    """Every closed paper trade so far, per strategy: what it earned, and whether that is distinguishable from
    what the backtest said it would earn. This is the learning loop - evidence accumulates, the rules do not
    drift with it; a rule changes only by a new pre-registered version."""
    where = "WHERE exit_ts IS NOT NULL" + (f" AND mode = '{mode}'" if mode else "")
    rows = con.execute(f"""SELECT strategy, mode, day, net_pnl, gross_pnl, costs, r_multiple, entry_px * qty AS notional,
                                  shortfall_bp, spread_bp
                           FROM trades {where}""").fetchall()
    out = []
    for strat in sorted({r[0] for r in rows}):
        for md in sorted({r[1] for r in rows if r[0] == strat}):
            rs = [r for r in rows if r[0] == strat and r[1] == md]
            bp = np.array([r[3] / r[7] * 1e4 for r in rs if r[7]])
            days = len({r[2] for r in rs})
            se = bp.std(ddof=1) / np.sqrt(len(bp)) if len(bp) > 1 else float("nan")
            exp = BACKTEST_NET_BP.get(strat)
            if len(bp) < 100:
                status = "COLLECTING"
            elif exp is not None and abs(bp.mean() - exp) > 2 * se:
                status = "DIFFERS_FROM_BACKTEST"
            elif bp.mean() + 2 * se < 0:
                status = "LOSING"
            elif bp.mean() - 2 * se > 0:
                status = "WINNING"
            else:
                status = "NO_EDGE_DETECTED"
            out.append({"strategy": strat, "mode": md, "trades": len(rs), "days": days,
                        "net_inr": float(sum(r[3] for r in rs)), "gross_inr": float(sum(r[4] for r in rs)),
                        "costs_inr": float(sum(r[5] for r in rs)), "net_bp_per_trade": float(bp.mean()) if len(bp) else float("nan"),
                        "se_bp": float(se), "mean_r": float(np.mean([r[6] for r in rs])),
                        "win_rate": float(np.mean([r[3] > 0 for r in rs])), "backtest_bp": exp, "status": status,
                        "shortfall_bp": float(np.nanmean([r[8] for r in rs if r[8] is not None])) if any(r[8] is not None for r in rs) else None,
                        "spread_bp": float(np.nanmean([r[9] for r in rs if r[9] is not None])) if any(r[9] is not None for r in rs) else None})
    return out


def write_report(con, day: str, mode: str, eng: I.DayEngine) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    p = REPORTS / f"{day}-{mode}.md"
    lines = [f"# Paper trading {day} ({mode}, {I.VERSION})", "",
             "Paper only: no order was placed. Costs are the intraday schedule plus impact; intraday profit is "
             "taxed as business income at your slab rate, not modelled per trade here.", ""]
    for strat, s in eng.summary().items():
        if not s.get("trades"):
            lines.append(f"- **{strat}**: no trades")
            continue
        lines.append(f"- **{strat}**: {s['trades']} trades, {s['wins']} winners net, gross Rs {s['gross']:,.0f}, "
                     f"costs Rs {s['costs']:,.0f}, **net Rs {s['net']:,.0f}**, mean R {s['mean_r']:+.2f}, "
                     f"exits {s['by_exit']}")
    lines += ["", f"Skipped signals: {len(eng.skipped)} (exposure limit, or price already beyond its stop).", "",
              "## Evidence so far, all days"]
    for e in evidence(con):
        lines.append(f"- {e['strategy']} ({e['mode']}): {e['trades']} trades over {e['days']} day(s), net "
                     f"{e['net_bp_per_trade']:+.1f} bp a trade (se {e['se_bp']:.1f}), win {e['win_rate']:.0%}, "
                     f"backtest said {e['backtest_bp']} bp -> **{e['status']}**"
                     + (f"; execution cost {e['shortfall_bp']:.1f} bp a trade (spread {e['spread_bp']:.1f} bp)"
                        if e.get("shortfall_bp") is not None else ""))
    lines += ["", "## Did the 09:30 board's top five beat the names it passed over?"]
    for r in BD.scorecard(con):
        if not r.get("days"):
            lines.append(f"- {r['horizon']}: not resolved yet")
        else:
            lines.append(f"- {r['horizon']}: top five {r['top']:+.2%}, the rest {r['rest']:+.2%}, spread "
                         f"{r['spread']:+.2%} (t {r['t']:+.2f}, {r['days']} days)")
    p.write_text("\n".join(lines), encoding="utf-8")
    return p
