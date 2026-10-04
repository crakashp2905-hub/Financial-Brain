"""Live ticks into the same minute bars the historical feed produces.

The streaming connection itself is the owner's to open: it needs the owner's authenticated session,
which this system never holds. What this module does is the part that must be right whatever feeds it:
turning a stream of ``(instrument, time, last price, cumulative volume)`` into **complete minute bars**
that join ``minute_bars`` exactly, so the intraday features, the execution simulator and the opportunity
engine see one series, not a historical one and a live one with a seam.

## Three things a tick aggregator gets wrong

**Volume is cumulative.** Kite reports volume traded *for the day* on every tick. A minute's volume is
the difference between the last cumulative value in the minute and the last one in the previous minute,
and a naive sum of tick volumes overcounts by orders of magnitude.

**A minute is only complete when the next one starts.** Emitting a bar on its last tick would emit a
partial bar whenever the final tick of a minute arrives late. A bar is released only once a tick from a
later minute has been seen, or on an explicit flush at the close.

**Time must be exchange time.** Ticks are placed by their exchange timestamp, not by arrival. A
network delay otherwise moves a trade into the next minute and changes which bar it opens or closes.
Out-of-order ticks older than the open bar are dropped and counted, not folded in.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

SOURCE = "KITE_TICK"


class TickError(ValueError):
    pass


@dataclass
class _Bar:
    minute: datetime
    open: float
    high: float
    low: float
    close: float
    cum_volume_last: int


@dataclass
class Aggregator:
    """Per-instrument minute bars from ticks. Feed ticks; collect the bars it releases."""

    _open: dict = field(default_factory=dict)         #: token -> the in-progress bar
    _carry: dict = field(default_factory=dict)        #: token -> cumulative volume at last release
    dropped_late: int = 0

    @staticmethod
    def _floor(ts: datetime) -> datetime:
        return ts.replace(second=0, microsecond=0)

    def feed(self, *, token: int, ts: datetime, price: float, cum_volume: int) -> list[dict]:
        if price <= 0:
            raise TickError(f"non-positive price {price} for token {token}")
        if cum_volume < 0:
            raise TickError(f"negative cumulative volume {cum_volume}")
        m = self._floor(ts)
        cur = self._open.get(token)
        released: list[dict] = []
        if cur is not None and m < cur.minute:
            self.dropped_late += 1
            return released
        if cur is not None and m > cur.minute:
            released.append(self._release(token, cur))
            cur = None
        if cur is None:
            self._open[token] = _Bar(m, price, price, price, price, cum_volume)
        else:
            cur.high, cur.low = max(cur.high, price), min(cur.low, price)
            cur.close, cur.cum_volume_last = price, cum_volume
        return released

    def _release(self, token: int, bar: _Bar) -> dict:
        prev = self._carry.get(token)
        # The first bar seen has no previous cumulative value: its volume is unknown, not the whole
        # day's volume so far. Report 0 rather than inventing a figure.
        volume = max(bar.cum_volume_last - prev, 0) if prev is not None else 0
        self._carry[token] = bar.cum_volume_last
        return {"instrument_token": token, "ts": bar.minute, "open": bar.open, "high": bar.high,
                "low": bar.low, "close": bar.close, "volume": volume, "source": SOURCE}

    def flush(self) -> list[dict]:
        """Release every in-progress bar (end of session)."""
        out = [self._release(t, b) for t, b in sorted(self._open.items())]
        self._open.clear()
        return out


def store(con, bars: list[dict], *, tradingsymbols: dict[int, tuple[str, str]] | None = None) -> int:
    """Insert released bars into ``minute_bars``, never overwriting a bar the historical feed wrote."""
    if not bars:
        return 0
    syms = tradingsymbols or {}
    n = 0
    for b in bars:
        sym, isin = syms.get(b["instrument_token"], (None, None))
        before = con.execute("SELECT COUNT(*) FROM minute_bars WHERE instrument_token = ? AND ts = ?",
                             [b["instrument_token"], b["ts"]]).fetchone()[0]
        if before:
            continue
        con.execute("""INSERT INTO minute_bars (instrument_token, tradingsymbol, isin, ts, open, high,
                       low, close, volume, source) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    [b["instrument_token"], sym, isin, b["ts"], b["open"], b["high"], b["low"],
                     b["close"], b["volume"], b["source"]])
        n += 1
    return n
