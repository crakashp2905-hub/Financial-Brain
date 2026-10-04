"""5-, 15- and 30-minute bars resampled from the stored minute bars.

One resampling, in SQL, so every consumer sees the same bars: the open is the first minute's open, the
close the last minute's close, the high and low the extremes, the volume the sum. The bucket is the
bar's **start** time, and a bucket is only emitted once all of its minutes are in the table - a partial
final bucket (a session cut off at 15:25, a live bar still forming) would otherwise look like a real
5-minute bar with a quarter of its volume.

Sessions open at 09:15 IST, which is a multiple of 5, 15 and 30 minutes from midnight, so epoch-aligned
buckets fall on the exchange's own boundaries.
"""
from __future__ import annotations

ALLOWED = (5, 15, 30)


class BarsError(ValueError):
    pass


def resample(con, minutes: int, *, source: str = "minute_bars") -> str:
    """Materialise ``bars_{minutes}m`` and return its name."""
    if minutes not in ALLOWED:
        raise BarsError(f"minutes must be one of {ALLOWED}, got {minutes}")
    name = f"bars_{minutes}m"
    con.execute(f"DROP TABLE IF EXISTS {name}")
    con.execute(f"""
        CREATE TABLE {name} AS
        SELECT instrument_token, ANY_VALUE(tradingsymbol) AS tradingsymbol, ANY_VALUE(isin) AS isin,
               time_bucket(INTERVAL '{minutes} minutes', ts) AS ts,
               ARG_MIN(open, ts) AS open, MAX(high) AS high, MIN(low) AS low,
               ARG_MAX(close, ts) AS close, SUM(volume) AS volume, COUNT(*) AS minutes
        FROM {source}
        GROUP BY instrument_token, time_bucket(INTERVAL '{minutes} minutes', ts)
        HAVING COUNT(*) = {minutes}
    """)
    return name
