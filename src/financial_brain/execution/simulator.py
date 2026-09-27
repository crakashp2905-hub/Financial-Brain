"""Walk an order through the minute bars it would actually have competed with.

The model is deliberately one line of economics: an order may take ``participation`` of each
minute's traded volume, at that minute's typical price, until it is done or the session ends.
Everything else is bookkeeping.

    shares_this_minute = min(remaining, participation * volume)
    price_this_minute   = the minute's typical price
    achieved            = volume-weighted average over the minutes consumed

## Why the typical price and not the close

A minute's close is one print. An order working through that minute gets something nearer its
average, and the standard estimate of a bar's average trade price is the **typical price**,
`(high + low + close) / 3`. Using the close would make a fill look better or worse depending on
which way the minute happened to end, which is noise the order did not experience.

For a **buy** the typical price is still optimistic - a marketable buy pays the offer, and the
prints in a rising minute skew toward it. That is the unmeasured spread named in the package
docstring, not something this module can correct.

## Why participation is the only parameter

Because it is the only one a trader controls. Spread and impact are the market's; the share of
volume you are willing to be is yours. Reporting it with every fill makes the assumption visible
instead of letting a default carry the result.

At 10% participation an order is a tenth of everything that trades until it is done - aggressive
for a real desk and deliberately so, because the question these fills answer is whether an edge
survives *optimistic* execution. If it dies at 10% it dies at 3%.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

#: Share of each minute's traded volume an order may take.
DEFAULT_PARTICIPATION = 0.10
#: NSE cash equity trades 09:15-15:30 IST.
SESSION_MINUTES = 375
#: Minutes before the close after which a buy will not start: an order that cannot finish has
#: bought a position it must then carry overnight, which is a different trade from the one the
#: signal asked for.
NO_NEW_ORDERS_AFTER = 360

BUY, SELL = "BUY", "SELL"


class ExecutionError(ValueError):
    pass


@dataclass
class Fill:
    symbol: str
    session: date
    side: str
    decision_price: float
    target_shares: int
    filled_shares: int = 0
    notional: float = 0.0
    minutes: int = 0
    first_minute: int | None = None
    last_minute: int | None = None
    participation: float = DEFAULT_PARTICIPATION
    slices: list[dict] = field(default_factory=list)

    @property
    def achieved(self) -> float | None:
        """Volume-weighted average price actually accumulated."""
        return (self.notional / self.filled_shares) if self.filled_shares else None

    @property
    def unfilled(self) -> int:
        return self.target_shares - self.filled_shares

    @property
    def fill_rate(self) -> float:
        return self.filled_shares / self.target_shares if self.target_shares else 0.0

    @property
    def shortfall_bps(self) -> float | None:
        """Implementation shortfall against the decision price, signed so positive is a cost.

        A buy that averages above the price that triggered it has paid a cost; a sell that averages
        below it has. The sign convention is "positive hurts", which is the only one that survives
        being read quickly.
        """
        a = self.achieved
        if a is None or not self.decision_price:
            return None
        raw = (a - self.decision_price) / self.decision_price
        return (raw if self.side == BUY else -raw) * 10_000

    def as_dict(self) -> dict:
        return {"symbol": self.symbol, "session": self.session, "side": self.side,
                "decision_price": self.decision_price,
                "target_shares": self.target_shares, "filled_shares": self.filled_shares,
                "achieved": self.achieved, "unfilled": self.unfilled,
                "fill_rate": self.fill_rate, "minutes": self.minutes,
                "first_minute": self.first_minute, "last_minute": self.last_minute,
                "shortfall_bps": self.shortfall_bps,
                "participation": self.participation,
                "notional": self.notional}


def session_bars(con, symbol: str, session: date) -> list[dict]:
    """One session's minute bars, in order, with the typical price precomputed."""
    rows = con.execute("""
        SELECT ts, open, high, low, close, volume
        FROM minute_bars
        WHERE tradingsymbol = ? AND ts::DATE = ?
          AND volume > 0 AND high >= low AND low > 0
        ORDER BY ts
    """, [symbol, session]).fetchall()
    return [{"i": i, "ts": r[0], "open": r[1], "high": r[2], "low": r[3],
             "close": r[4], "volume": int(r[5]),
             # (H+L+C)/3 - the standard estimate of a bar's average trade price. A minute's
             # close is one print and an order working through the minute gets nearer this.
             "typical": (r[2] + r[3] + r[4]) / 3.0}
            for i, r in enumerate(rows)]


def execute(con, *, symbol: str, session: date, side: str, target_shares: int,
            decision_price: float, start_minute: int = 0,
            participation: float = DEFAULT_PARTICIPATION,
            bars: list[dict] | None = None,
            no_new_after: int = NO_NEW_ORDERS_AFTER) -> Fill:
    """Walk ``target_shares`` through the session's real volume from ``start_minute``.

    ``start_minute`` is the minute **after** the one that produced the signal. A fill that begins
    in the same minute the signal was computed is reading a price it could not have acted on, which
    is the cheapest look-ahead available in an execution study.
    """
    if side not in (BUY, SELL):
        raise ExecutionError("side must be BUY or SELL")
    if target_shares <= 0:
        raise ExecutionError("target_shares must be positive")
    if not 0 < participation <= 1:
        raise ExecutionError(f"participation {participation} must be in (0, 1]")

    rows = bars if bars is not None else session_bars(con, symbol, session)
    f = Fill(symbol=symbol, session=session, side=side, decision_price=decision_price,
             target_shares=target_shares, participation=participation)
    if not rows:
        return f
    if start_minute >= no_new_after:
        # Refused rather than filled late: an order that cannot finish leaves a position the
        # signal did not ask for.
        f.slices.append({"refused": f"start minute {start_minute} is past {no_new_after}"})
        return f

    remaining = target_shares
    for bar in rows:
        if bar["i"] < start_minute:
            continue
        if remaining <= 0:
            break
        take = int(min(remaining, participation * bar["volume"]))
        if take <= 0:
            continue
        f.notional += take * bar["typical"]
        f.filled_shares += take
        remaining -= take
        f.minutes += 1
        f.first_minute = bar["i"] if f.first_minute is None else f.first_minute
        f.last_minute = bar["i"]
        f.slices.append({"minute": bar["i"], "shares": take, "price": bar["typical"],
                         "bar_volume": bar["volume"]})
    return f


def round_trip(con, *, symbol: str, session: date, entry_minute: int,
               decision_price: float, target_shares: int,
               exit_minute: int | None = None,
               participation: float = DEFAULT_PARTICIPATION) -> dict:
    """Buy and square off inside the session, both legs walked through real volume.

    The exit is sized to what the entry **actually filled**, not to what it wanted. An exit that
    sells the intended quantity rather than the held one is selling stock the strategy never
    owned, and it flatters the result by exactly the amount the entry fell short.
    """
    bars = session_bars(con, symbol, session)
    if not bars:
        return {"symbol": symbol, "session": session, "note": "no minute bars"}

    buy = execute(con, symbol=symbol, session=session, side=BUY,
                  target_shares=target_shares, decision_price=decision_price,
                  start_minute=entry_minute, participation=participation, bars=bars)
    if not buy.filled_shares:
        return {"symbol": symbol, "session": session, "entry": buy.as_dict(),
                "exit": None, "gross_return": None,
                "note": "nothing filled; no position to exit"}

    # Square off with enough time to finish. Selling into the last minute at any participation
    # rate is the one fill a real desk cannot rely on.
    start_exit = exit_minute if exit_minute is not None else max(
        buy.last_minute + 1, len(bars) - 30)
    sell = execute(con, symbol=symbol, session=session, side=SELL,
                   target_shares=buy.filled_shares,
                   decision_price=bars[-1]["close"],
                   start_minute=min(start_exit, len(bars) - 1),
                   participation=participation, bars=bars,
                   no_new_after=len(bars))

    gross = None
    if sell.achieved and buy.achieved:
        gross = sell.achieved / buy.achieved - 1
    return {
        "symbol": symbol, "session": session,
        "entry": buy.as_dict(), "exit": sell.as_dict(),
        "gross_return": gross,
        "entry_shortfall_bps": buy.shortfall_bps,
        "exit_shortfall_bps": sell.shortfall_bps,
        "total_shortfall_bps": (
            (buy.shortfall_bps or 0) + (sell.shortfall_bps or 0)),
        "held_shares": buy.filled_shares,
        "unfilled_entry": buy.unfilled,
        "unsold": sell.unfilled,
        "minutes_to_enter": buy.minutes,
        "minutes_to_exit": sell.minutes,
    }


def capacity(con, *, symbol: str, session: date,
             participation: float = DEFAULT_PARTICIPATION,
             from_minute: int = 0) -> dict:
    """The largest order this name could absorb on this session at this participation rate.

    Measured, not estimated: the sum of ``participation x volume`` over the remaining minutes, at
    those minutes' own prices. This is the number a position limit should be checked against, and
    it is a fact about a session rather than a bucket average.
    """
    bars = session_bars(con, symbol, session)
    usable = [b for b in bars if b["i"] >= from_minute]
    if not usable:
        return {"symbol": symbol, "session": session, "shares": 0, "notional": 0.0,
                "minutes": 0}
    shares = sum(int(participation * b["volume"]) for b in usable)
    notional = sum(int(participation * b["volume"]) * b["typical"] for b in usable)
    return {"symbol": symbol, "session": session, "participation": participation,
            "minutes": len(usable), "shares": shares, "notional": notional,
            "session_volume": sum(b["volume"] for b in usable),
            "session_notional": sum(b["volume"] * b["typical"] for b in usable)}
