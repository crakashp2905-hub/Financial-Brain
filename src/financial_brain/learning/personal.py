"""The owner's own trading mistakes belong in the same loop.

The paper strategies are not the only place mistakes are made. ``portfolio/behaviour_review`` already reads the owner's real
trades from the Kite trade book and looks for five habits (overtrading, averaging down, selling winners early, chasing events,
ignoring one's own stop) and keeps a habit in its *anti-playbook* only when it has at least eight round trips, a negative mean net
return and t <= -2, with the number of habits examined printed beside it. This module reads that review as one more source of
lessons: it adds the result to the status view, the terminal panel and the digest, and raises an alert once for each habit that
newly enters the anti-playbook. Like everything here it warns; it never applies anything.

The journal is empty until the owner imports their trade book (``fb journal --import <file>``; Kite returns only the current day),
and the summary says so rather than inventing a finding.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

BOOK = Path("data") / "my_portfolio.duckdb"
EMPTY = {"trades": 0, "trips": 0, "habits": {}, "overtrading_days": [], "averaging_down": 0, "avoid": [], "examined": 0, "note": ""}


def summary(book_path: Path = BOOK, research_db: Path | None = None) -> dict:
    """The owner's habit review, or an honest empty one. Never raises: a missing journal or database is a sentence."""
    try:
        if not Path(book_path).exists():
            return {**EMPTY, "note": "no journal yet"}
        con = duckdb.connect(str(book_path), read_only=True)
        try:
            t = con.execute("SELECT trade_id, ts, day, tradingsymbol, product, side, quantity, price FROM my_trades").df()
        finally:
            con.close()
        if t.empty:
            return {**EMPTY, "note": "the journal is empty: import your Kite trade book (fb journal --import <file>)"}
        from ..portfolio import behaviour_review as BRV
        from ..portfolio import journal as J
        trips = J.round_trips(t)
        if trips.empty or research_db is None:
            return {**EMPTY, "trades": int(len(t)), "trips": int(len(trips)), "note": "no closed round trips to review yet"}
        rcon = duckdb.connect(str(research_db), read_only=True)
        try:
            at = J.attribute(rcon, trips)

            def closes(sym):
                d = rcon.execute("SELECT business_date, close_adj FROM adjusted_prices WHERE ticker = ? ORDER BY business_date",
                                 [sym]).df()
                return d.set_index(pd.to_datetime(d["business_date"]))["close_adj"] if len(d) else None
            ev: dict = {}
            for sym, day in rcon.execute("""SELECT DISTINCT p.ticker, a.business_date FROM announcements a
                    JOIN (SELECT DISTINCT isin, ticker FROM adjusted_prices) p USING (isin)
                    WHERE a.materiality = 'high' AND p.ticker IN (SELECT UNNEST(?))""", [list(set(trips["symbol"]))]).fetchall():
                ev.setdefault(sym, set()).add(day)
            rv = BRV.review(t, at.assign(net=at["net"]) if "net" in at else trips, closes, events=ev)
        finally:
            rcon.close()
        ap = rv["anti_playbook"]
        return {"trades": int(len(t)), "trips": int(len(trips)), "habits": rv["tag_counts"],
                "overtrading_days": rv["overtrading_days"], "averaging_down": len(rv["averaging_down"]),
                "avoid": ap["entries"], "examined": ap.get("examined", 0), "note": ap.get("note", "")}
    except Exception as e:                              # noqa: BLE001 - the personal review is extra; it must not stop the loop
        return {**EMPTY, "note": f"not available ({type(e).__name__}: {str(e)[:80]})"}


def lines(s: dict) -> list[str]:
    """The summary as status lines."""
    if not s.get("trades"):
        return [f"  {s.get('note') or 'no journal yet'}"]
    out = [f"  {s['trades']} trades, {s['trips']} closed round trips. Habits seen: "
           + (", ".join(f"{k} {v}" for k, v in s["habits"].items()) or "none") + f"; days over the fill limit: "
           f"{len(s['overtrading_days'])}; averaging down: {s['averaging_down']}."]
    for e in s["avoid"]:
        out.append(f"  AVOID {e['tag']}: {e['trips']} trips, mean net {e['mean_net'] * 100:+.2f}%, t {e['t']:.1f}")
    if not s["avoid"]:
        out.append("  no habit is losing at t <= -2 yet (it needs at least 8 round trips of one habit)")
    return out


def needs_you(s: dict) -> list[str]:
    return [f"Your own habit '{e['tag']}' keeps losing after costs ({e['trips']} round trips, mean {e['mean_net'] * 100:+.2f}%, "
            f"t {e['t']:.1f}; {s.get('note', '')}). It is a warning, not a rule." for e in s.get("avoid", [])]


def alerts(s: dict) -> list[dict]:
    return [{"key": f"learn-habit:{e['tag']}", "title": "One of your own habits keeps losing",
             "body": f"'{e['tag']}': {e['trips']} round trips, mean net {e['mean_net'] * 100:+.2f}%, t {e['t']:.1f}. Warning only."}
            for e in s.get("avoid", [])]
