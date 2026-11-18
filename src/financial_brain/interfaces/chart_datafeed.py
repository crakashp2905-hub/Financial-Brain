"""Read-only TradingView UDF history projection over archived Kite minute bars.

No live API request, chart asset, or order route is involved. Chart timestamps are
UTC epoch seconds; the archive keeps the original timezone-aware Kite timestamps.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone


class ChartDataError(ValueError):
    """A chart request or archived bar is unsafe to display as OHLCV."""


RESOLUTIONS = ("1",)
MAX_WINDOW_S = 7 * 86400
MAX_BARS = 10000


def configuration() -> dict:
    return {"supports_search": True, "supports_group_request": False,
            "supports_marks": False, "supports_timescale_marks": False,
            "supports_time": False, "supported_resolutions": list(RESOLUTIONS)}


def symbol_info(con, token: int) -> dict:
    if isinstance(token, bool) or not isinstance(token, int) or token <= 0:
        raise ChartDataError("invalid instrument token")
    rows = con.execute("""SELECT DISTINCT tradingsymbol FROM minute_bars
        WHERE instrument_token = ? AND tradingsymbol IS NOT NULL LIMIT 2""", [token]).fetchall()
    if len(rows) != 1:
        raise ChartDataError("instrument identity missing or ambiguous")
    name = rows[0][0]
    return {"name": name, "ticker": f"KITE:{token}", "description": name,
            "type": "stock", "session": "0915-1530", "timezone": "Asia/Kolkata",
            "exchange": "KITE_ARCHIVE", "listed_exchange": "KITE_ARCHIVE",
            "minmov": 1, "pricescale": 100, "has_intraday": True,
            "supported_resolutions": list(RESOLUTIONS), "volume_precision": 0,
            "data_status": "endofday"}


def history(con, *, token: int, resolution: str, start: int, end: int) -> dict:
    """Project one instrument's stored bars in the half-open [start, end) range.

    Only 1-minute bars are currently supported. Other resolutions require an
    explicit session-aware aggregation, not a browser-side interpolation.
    """
    if isinstance(token, bool) or not isinstance(token, int) or token <= 0:
        raise ChartDataError("invalid instrument token")
    if resolution != "1":
        raise ChartDataError("resolution not yet available from archive")
    if any(isinstance(x, bool) or not isinstance(x, int) for x in (start, end)):
        raise ChartDataError("invalid time range")
    if start < 0 or end <= start or end - start > MAX_WINDOW_S:
        raise ChartDataError("time range exceeds seven days")
    try:
        rows = con.execute("""SELECT ts, open, high, low, close, volume
            FROM minute_bars WHERE instrument_token = ? AND ts >= ? AND ts < ?
            ORDER BY ts LIMIT ?""", [token, datetime.fromtimestamp(start, timezone.utc),
                                        datetime.fromtimestamp(end, timezone.utc), MAX_BARS + 1]).fetchall()
    except (OverflowError, OSError) as exc:
        raise ChartDataError("invalid time range") from exc
    if len(rows) > MAX_BARS:
        raise ChartDataError("too many bars; narrow the range")
    if not rows:
        return {"s": "no_data"}
    out = {"s": "ok", "t": [], "o": [], "h": [], "l": [], "c": [], "v": []}
    previous = None
    for ts, opening, high, low, close, volume in rows:
        if ts.tzinfo is None:
            raise ChartDataError("bar timestamp lacks timezone")
        stamp = int(ts.timestamp())
        values = (opening, high, low, close)
        if (previous is not None and stamp <= previous or
            any(v is None or not math.isfinite(v) or v <= 0 for v in values) or
            low > min(opening, close) or high < max(opening, close) or low > high or
            volume is None or volume < 0):
            raise ChartDataError("invalid archived OHLCV")
        previous = stamp
        for key, value in zip(("t", "o", "h", "l", "c", "v"),
                              (stamp, opening, high, low, close, volume), strict=True):
            out[key].append(value)
    return out
