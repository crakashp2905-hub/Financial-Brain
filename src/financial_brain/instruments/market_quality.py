"""Market data quality and derived market facts (FB-0204, FB-0206, FB-0208, FB-0304, FB-0406,
FB-0469, FB-1803, FB-1905).

* ``eod_rows``: EOD rows tagged with source and content hash; a re-delivery with different values is a
  correction kept alongside the original, never an overwrite.
* ``bars_from_ticks``: session-gated minute bars from ticks; out-of-session ticks dropped.
* ``liquidity``: effective spread, depth, median traded value, stale-quote rate, gap count.
* ``NavRevisions``: NAV revision history with publication and ingestion times.
* ``dedupe_alerts``: repeated alerts collapse unless rule version or price materially changed.
* ``tick_ok``: source/receipt timestamps, age, spread, depth, circuit and halt checks.
* ``sequence_gaps``: missing sequence numbers force no-entry and a reconciliation.
* ``gap_open_fill``: opening gap and circuit-limit aware fill for stops.
"""
from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta


class QualityError(ValueError):
    pass


def eod_rows(rows: list[dict], source: str) -> list[dict]:
    out = []
    for r in rows:
        h = hashlib.sha256(json.dumps(r, sort_keys=True, default=str).encode()).hexdigest()
        out.append({**r, "source": source, "content_hash": h})
    return out


def merge_corrections(existing: list[dict], incoming: list[dict]) -> list[dict]:
    """Keep every distinct version per (symbol, date); a changed row is appended as a correction."""
    seen = {(r["symbol"], r["date"], r["content_hash"]) for r in existing}
    out = list(existing)
    for r in incoming:
        k = (r["symbol"], r["date"], r["content_hash"])
        if k in seen:
            continue
        prior = [x for x in out if (x["symbol"], x["date"]) == (r["symbol"], r["date"])]
        out.append({**r, "correction_of": prior[-1]["content_hash"] if prior else None})
        seen.add(k)
    return out


def bars_from_ticks(ticks: list[tuple[datetime, float, int]], open_t: time, close_t: time, tz
                    ) -> list[dict]:
    bars: dict[datetime, dict] = {}
    for ts, px, qty in sorted(ticks):
        local = ts.astimezone(tz)
        if not (open_t <= local.time() < close_t):
            continue
        k = local.replace(second=0, microsecond=0)
        b = bars.setdefault(k, {"start": k, "open": px, "high": px, "low": px, "close": px, "volume": 0})
        b["high"], b["low"], b["close"] = max(b["high"], px), min(b["low"], px), px
        b["volume"] += qty
    return [bars[k] for k in sorted(bars)]


def liquidity(quotes: list[tuple[datetime, float, float, int, int]], trades_value: list[float],
              now: datetime, stale_after: timedelta) -> dict[str, float]:
    if not quotes:
        raise QualityError("no quotes")
    spreads = [(a - b) / ((a + b) / 2) * 1e4 for _, b, a, _, _ in quotes if a > b > 0]
    depth = [min(bq, aq) for _, _, _, bq, aq in quotes]
    gaps = sum(1 for (t0, *_), (t1, *_) in zip(quotes, quotes[1:], strict=False) if t1 - t0 > stale_after)
    stale = sum(1 for t, *_ in quotes if now - t > stale_after)
    return {"median_spread_bps": statistics.median(spreads) if spreads else float("inf"),
            "median_depth": statistics.median(depth),
            "median_traded_value": statistics.median(trades_value) if trades_value else 0.0,
            "stale_quote_rate": stale / len(quotes), "gaps": float(gaps)}


@dataclass
class NavRevisions:
    rows: dict[tuple[str, date], list[tuple[float, datetime, datetime]]] = field(default_factory=dict)

    def add(self, code: str, nav_date: date, nav: float, published_at: datetime, ingested_at: datetime) -> None:
        hist = self.rows.setdefault((code, nav_date), [])
        if hist and hist[-1][0] == nav:
            return
        hist.append((nav, published_at, ingested_at))

    def as_known(self, code: str, nav_date: date, at: datetime) -> float | None:
        """The value known at ``at``; a later correction is not backdated."""
        known = [n for n, p, i in self.rows.get((code, nav_date), []) if max(p, i) <= at]
        return known[-1] if known else None


def dedupe_alerts(alerts: list[dict], price_move: float) -> list[dict]:
    out: list[dict] = []
    last: dict[str, dict] = {}
    for a in sorted(alerts, key=lambda x: x["at"]):
        k = f"{a['signal']}:{a['kind']}"
        p = last.get(k)
        if p and p["rule_version"] == a["rule_version"] and abs(a["price"] - p["price"]) / p["price"] < price_move:
            continue
        out.append(a)
        last[k] = a
    return out


@dataclass(frozen=True)
class Tick:
    bid: float
    ask: float
    bid_qty: int
    ask_qty: int
    exchange_ts: datetime | None
    receipt_ts: datetime
    upper_circuit: float | None = None
    lower_circuit: float | None = None
    halted: bool = False


def tick_ok(t: Tick, now: datetime, *, max_age: timedelta, max_spread_bps: float, min_depth: int
            ) -> list[str]:
    """Reasons a tick cannot trigger a fill. An LTP-only tick never passes."""
    why = []
    if t.halted:
        why.append("halted")
    if t.exchange_ts is None:
        why.append("no exchange timestamp")
    elif now - t.exchange_ts > max_age or t.receipt_ts > now:
        why.append("stale or future")
    if t.bid <= 0 or t.ask <= t.bid:
        why.append("no two-sided book")
    elif (t.ask - t.bid) / ((t.ask + t.bid) / 2) * 1e4 > max_spread_bps:
        why.append("spread too wide")
    if min(t.bid_qty, t.ask_qty) < min_depth:
        why.append("thin depth")
    if t.upper_circuit is not None and t.ask >= t.upper_circuit:
        why.append("at upper circuit")
    if t.lower_circuit is not None and t.bid <= t.lower_circuit:
        why.append("at lower circuit")
    return why


def sequence_gaps(seqs: list[int]) -> list[tuple[int, int]]:
    s = sorted(set(seqs))
    return [(a, b) for a, b in zip(s, s[1:], strict=False) if b - a > 1]


def gap_open_fill(side_long: bool, stop: float, open_px: float, lower_circuit: float | None,
                  upper_circuit: float | None) -> float | None:
    """Stop fill at a gapped open; None when locked at circuit (no fill possible)."""
    if side_long:
        if lower_circuit is not None and open_px <= lower_circuit:
            return None
        return min(open_px, stop)
    if upper_circuit is not None and open_px >= upper_circuit:
        return None
    return max(open_px, stop)
