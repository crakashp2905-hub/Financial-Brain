"""Pre-trade checks. Every order passes all of them or none is sent; the reasons are kept on the order.

    session       inside 09:15-15:30 IST on a weekday (no pre-open, no after-market orders)
    instrument    exchange and product allowed by the stage; symbol on the allowlist; not on today's F&O ban list
    shape         whole shares, a limit price on the tick grid
    price band    the limit within ``price_band`` of the last price (a fat-finger price never leaves)
    size          order value <= ``max_order_inr``; position after the order <= ``max_position_inr``
    day           orders today < ``max_orders_per_day``; notional today + this <= ``max_daily_notional_inr``
    gross         deployed capital after a buy <= ``max_gross_inr``
    duplicates    no live order already working the same symbol and side
    rate          fewer than ``max_orders_per_minute`` in the last 60 seconds

``Limits`` come from the rollout stage (``rollout.limits``); the gate never chooses its own.

The ban list is NSE's daily "securities in ban period" file. A name in the ban period may not take new F&O
positions; this gate also refuses cash orders in it, which the rules allow but which means trading into a name the
exchange has flagged for excess open interest - a conservative choice, stated.
"""
from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone

from . import model as M

IST = timezone(timedelta(hours=5, minutes=30))
OPEN, CLOSE = time(9, 15), time(15, 30)
TICK = 0.05
BAN_URL = "https://nsearchives.nseindia.com/content/fo/fo_secban.csv"


@dataclass(frozen=True)
class Limits:
    max_order_inr: float
    max_position_inr: float
    max_daily_notional_inr: float
    max_gross_inr: float
    max_orders_per_day: int = 50
    max_orders_per_minute: int = 20
    price_band: float = 0.01
    exchanges: frozenset = frozenset({"NSE"})
    products: frozenset = frozenset({"CNC"})
    allowlist: frozenset | None = None          #: None = every symbol not banned


@dataclass
class Context:
    """What the gate needs to know besides the order. The engine builds it from the journal and the broker."""
    now: datetime
    ltp: float | None
    position_qty: int = 0                       #: current net shares in this symbol
    gross_inr: float = 0.0                      #: value of everything held now
    orders_today: int = 0
    notional_today: float = 0.0
    live_same_side: bool = False
    recent_order_times: list = field(default_factory=list)
    banned: frozenset = frozenset()
    tick: float = TICK


def check(o: M.Order, lim: Limits, cx: Context) -> list[str]:
    """Every failed check, in words. Empty means the order may proceed."""
    bad = []
    t = cx.now.astimezone(IST)
    if t.weekday() >= 5 or not (OPEN <= t.time() <= CLOSE):
        bad.append(f"outside the session ({t:%a %H:%M} IST)")
    if o.exchange not in lim.exchanges:
        bad.append(f"exchange {o.exchange} not allowed at this stage")
    if o.product not in lim.products:
        bad.append(f"product {o.product} not allowed at this stage")
    if lim.allowlist is not None and o.symbol not in lim.allowlist:
        bad.append(f"{o.symbol} is not on the allowlist")
    if o.symbol in cx.banned:
        bad.append(f"{o.symbol} is in the F&O ban period")
    steps = o.limit_price / cx.tick
    if abs(steps - round(steps)) > 1e-6:
        bad.append(f"limit {o.limit_price} is not on the {cx.tick} tick grid")
    if cx.ltp is None or cx.ltp <= 0:
        bad.append("no last price: the price band cannot be checked")
    elif abs(o.limit_price / cx.ltp - 1) > lim.price_band:
        bad.append(f"limit {o.limit_price} is more than {lim.price_band:.1%} from the last price {cx.ltp}")
    if o.notional > lim.max_order_inr:
        bad.append(f"order value {o.notional:,.0f} > {lim.max_order_inr:,.0f}")
    after = cx.position_qty + (o.qty if o.side == M.BUY else -o.qty)
    if abs(after) * o.limit_price > lim.max_position_inr:
        bad.append(f"position after the order {abs(after) * o.limit_price:,.0f} > {lim.max_position_inr:,.0f}")
    if o.side == M.SELL and o.product == "CNC" and after < 0:
        bad.append("a delivery sell larger than the holding would be a short sale")
    if cx.orders_today >= lim.max_orders_per_day:
        bad.append(f"{cx.orders_today} orders today: the daily cap is {lim.max_orders_per_day}")
    if cx.notional_today + o.notional > lim.max_daily_notional_inr:
        bad.append(f"today's notional would be {cx.notional_today + o.notional:,.0f} > {lim.max_daily_notional_inr:,.0f}")
    if o.side == M.BUY and cx.gross_inr + o.notional > lim.max_gross_inr:
        bad.append(f"deployed capital would be {cx.gross_inr + o.notional:,.0f} > {lim.max_gross_inr:,.0f}")
    if cx.live_same_side:
        bad.append(f"a live {o.side} order for {o.symbol} is already working")
    recent = [x for x in cx.recent_order_times if (cx.now - x).total_seconds() < 60]
    if len(recent) >= lim.max_orders_per_minute:
        bad.append(f"{len(recent)} orders in the last minute: the cap is {lim.max_orders_per_minute}")
    return bad


def round_to_tick(price: float, side: str, tick: float = TICK) -> float:
    """A limit on the grid, rounded the conservative way (a buy down, a sell up)."""
    steps = price / tick
    n = math.floor(steps + 1e-9) if side == M.BUY else math.ceil(steps - 1e-9)
    return round(n * tick, 2)


def parse_ban_list(text: str) -> frozenset:
    """NSE's fo_secban.csv: a heading line, then ``n,SYMBOL`` rows. Anything else is ignored."""
    out = set()
    for row in csv.reader(io.StringIO(text)):
        if len(row) >= 2 and row[0].strip().isdigit() and re.fullmatch(r"[A-Z0-9&\-]+", row[1].strip()):
            out.add(row[1].strip())
    return frozenset(out)


def fetch_ban_list(opener=None) -> frozenset:
    """Today's ban list from NSE's archive host (the same host the bhavcopy comes from). Raises on failure: an order
    must not pass the gate because the ban list could not be read - the engine treats a failure as 'all banned'."""
    import urllib.request
    from ...providers.base import USER_AGENT
    req = urllib.request.Request(BAN_URL, headers={"User-Agent": USER_AGENT})
    with (opener or urllib.request.urlopen)(req, timeout=15) as r:
        return parse_ban_list(r.read().decode("utf-8", errors="replace"))
