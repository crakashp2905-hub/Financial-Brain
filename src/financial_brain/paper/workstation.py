"""Two workstation panels for the terminal: what is coming (a calendar) and what an order would look like before it
exists (a ticket preview with the risk gate's verdict).

Calendar, from what the store actually holds - nothing is invented to fill a gap:

    board meetings   BSE/NSE intimations ("scheduled on DD/MM/YYYY"), results meetings marked
    corporate actions ex-dates still ahead (``corporate_actions``)
    F&O expiry       stock and index monthly contracts: the last Tuesday of the month (NSE, from September 2025);
                     checked against the stored contracts' expiries
    macro            not collected (no RBI / budget / data-release calendar in the store) - said on the page

Ticket preview: for a name, the plan's shares and notional at the next session, the modelled one-side cost, and what
``execution.oms.risk_gate.check`` says against the stage-SMALL limits at 10:00 IST of the next session (the F&O ban
list is read at send time, not here). It is a preview: no order is created, journaled or sent.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone

import pandas as pd

IST = timezone(timedelta(hours=5, minutes=30))
SCHEDULED = re.compile(r"scheduled on (\d{1,2})/(\d{1,2})/(\d{4})", re.I)


def last_tuesday(y: int, m: int) -> date:
    d = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    while d.weekday() != 1:
        d -= timedelta(days=1)
    return d


def expiries(today: date, n: int = 2) -> list[date]:
    out, y, m = [], today.year, today.month
    while len(out) < n:
        d = last_tuesday(y, m)
        if d >= today:
            out.append(d)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def board_meetings(rcon, today: date, horizon: int = 21) -> pd.DataFrame:
    rows = rcon.execute("""SELECT company, isin, headline FROM announcements
                           WHERE event_type = 'BOARD_MEETING' AND business_date >= ?::DATE - INTERVAL 45 DAY""",
                        [today]).fetchall()
    out = []
    for company, isin, head in rows:
        m = SCHEDULED.search(head or "")
        if not m:
            continue
        try:
            d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            continue
        if today <= d <= today + timedelta(days=horizon):
            out.append({"date": d, "company": company, "isin": isin, "results": "result" in head.lower()})
    return pd.DataFrame(out, columns=["date", "company", "isin", "results"]).drop_duplicates(["date", "isin"])


def ex_dates(rcon, today: date, horizon: int = 21) -> pd.DataFrame:
    return rcon.execute("""SELECT ex_date AS date, isin, action_type, details FROM corporate_actions
                           WHERE ex_date BETWEEN ? AND ?::DATE + INTERVAL 21 DAY ORDER BY ex_date""",
                        [today, today]).df() if horizon else pd.DataFrame()


def next_session_10am(today: date) -> datetime:
    d = today + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return datetime.combine(d, time(10, 0), tzinfo=IST)


def ticket(*, symbol: str, price: float, shares: int, cost_one_side: float, when: datetime) -> dict:
    from ..execution.oms import model as M
    from ..execution.oms import risk_gate as RG
    from ..execution.oms import rollout as RO
    if shares < 1:
        return {"symbol": symbol, "shares": 0, "verdict": "below one share at this size"}
    limit = RG.round_to_tick(price * 1.0005, M.BUY)
    o = M.Order(client_id="preview", strategy="preview", day=str(when.date()), symbol=symbol, exchange="NSE",
                side=M.BUY, qty=int(shares), limit_price=limit, arrival_price=price)
    why = RG.check(o, RO.SMALL, RG.Context(now=when, ltp=price))
    return {"symbol": symbol, "shares": int(shares), "limit": limit, "notional": shares * limit,
            "cost_inr": shares * limit * cost_one_side, "verdict": "would pass" if not why else "; ".join(why)}
