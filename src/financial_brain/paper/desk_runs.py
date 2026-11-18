"""Desk run helpers: intraday long/short with mandatory flatten, and daily publication
(FB-0407, FB-0474, FB-1004, FB-1005).

* ``IntradayPosition`` handles BUY or SELL_SHORT with stop/target, adverse-first bars, and a forced
  ``SESSION_FLATTEN`` at the flatten time; a flatten with no price escalates as MISSED_EXIT.
* ``publish_daily``: up to five swing candidates plus an independent ETF rank list, locked for the
  next session; empty lists are valid.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal


class RunError(ValueError):
    pass


@dataclass
class IntradayPosition:
    side: str                 # BUY or SELL_SHORT
    qty: Decimal
    entry: Decimal
    stop: Decimal
    target: Decimal
    flatten_at: time
    tz: object
    events: list[tuple[str, datetime, Decimal | None]] = field(default_factory=list)
    open: bool = True

    def __post_init__(self) -> None:
        ok = (self.stop < self.entry < self.target) if self.side == "BUY" else \
             (self.target < self.entry < self.stop) if self.side == "SELL_SHORT" else False
        if not ok:
            raise RunError("invalid intraday plan")

    @property
    def close_action(self) -> str:
        return "SELL_TO_CLOSE" if self.side == "BUY" else "BUY_TO_COVER"

    def _close(self, why: str, at: datetime, px: Decimal | None) -> None:
        self.events.append((why, at, px))
        if px is not None:
            self.open = False

    def on_bar(self, at: datetime, o: Decimal | None, h: Decimal | None, lo: Decimal | None,
               c: Decimal | None) -> None:
        if not self.open:
            return
        local = at.astimezone(self.tz).time()  # type: ignore[arg-type]
        if local >= self.flatten_at:
            self._close("SESSION_FLATTEN" if c is not None else "MISSED_EXIT", at, c)
            return
        if None in (o, h, lo, c):
            return
        assert o is not None and h is not None and lo is not None
        long = self.side == "BUY"
        if (long and o <= self.stop) or (not long and o >= self.stop):
            return self._close("HARD_STOP", at, o)
        if (long and o >= self.target) or (not long and o <= self.target):
            return self._close("TAKE_PROFIT", at, o)
        hit_stop = lo <= self.stop if long else h >= self.stop
        hit_tp = h >= self.target if long else lo <= self.target
        if hit_stop:
            return self._close("HARD_STOP", at, self.stop)
        if hit_tp:
            return self._close("TAKE_PROFIT", at, self.target)

    def pnl(self) -> Decimal | None:
        fills = [p for w, _, p in self.events if p is not None]
        if not fills:
            return None
        d = fills[-1] - self.entry if self.side == "BUY" else self.entry - fills[-1]
        return d * self.qty


def publish_daily(swing: list[dict], etf_ranked: list[tuple[str, float]], *, for_session: str,
                  k: int = 5) -> dict:
    """Locked next-session publication; swing capped at k, ETF ranks kept independent."""
    return {"for_session": for_session, "locked": True,
            "swing": [c["symbol"] for c in sorted(swing, key=lambda c: c["net_ev"], reverse=True)
                      if c["net_ev"] > 0][:k],
            "etf_rank": [u for u, _ in etf_ranked]}
