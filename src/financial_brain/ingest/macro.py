"""Macro series from FRED (St. Louis Fed), free CSV: the rupee, rates, inflation and crude.

``https://fred.stlouisfed.org/graph/fredgraph.csv?id=<series>`` (robots.txt allows it). Each download is the whole
history; it goes to the raw lake (so a revised series is kept beside the old one) and replaces ``macro_series``.

**Not point in time.** FRED's date is the observation period, not the release date: India's CPI for a month is
published about two weeks later, and FRED serves the latest revision. A signal must lag each series by its
``release_lag_days`` at least, and treat monthly values as revised data.
"""
from __future__ import annotations

import csv
import io
import urllib.request
from datetime import date, datetime, timezone

from ..lake.store import RawLake

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"
#: series -> (description, release lag in days to assume)
SERIES = {
    "DEXINUS": ("USD-INR spot, noon buying rate in New York (daily)", 1),
    "DCOILBRENTEU": ("Brent crude, USD a barrel (daily)", 1),
    "INDIRLTLT01STM": ("India 10-year government bond yield, % (monthly)", 15),
    "IRSTCI01INM156N": ("India call money rate, % (monthly)", 15),
    "INDCPIALLMINMEI": ("India CPI, all items, index (monthly; OECD, ends 2025-03)", 45),
}
UA = "financial-brain/1.0 (personal research)"


def fetch(series: str) -> bytes:
    req = urllib.request.Request(URL.format(series), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def parse(payload: bytes) -> list[tuple[date, float]]:
    rows = list(csv.reader(io.StringIO(payload.decode("utf-8", "replace"))))
    out = []
    for r in rows[1:]:
        if len(r) < 2 or r[1] in ("", "."):
            continue
        try:
            out.append((date.fromisoformat(r[0]), float(r[1])))
        except ValueError:
            continue
    return out


def prefetch(cfg, *, fetch_fn=fetch) -> dict:
    lake = RawLake(cfg.lake)
    out = {}
    for s in SERIES:
        try:
            body = fetch_fn(s)
        except OSError as e:
            out[s] = f"failed: {e}"
            continue
        lake.put(source="FRED", dataset="macro", business_date=date.today(), filename=f"{s}.csv", payload=body,
                 url=URL.format(s), content_type="text/csv")
        out[s] = len(body)
    return out


def load(con, cfg, day: date | None = None) -> dict:
    lake = RawLake(cfg.lake)
    d = day or date.today()
    now = datetime.now(timezone.utc)
    out = {}
    for s, (desc, lag) in SERIES.items():
        key = f"FRED/macro/{d:%Y/%m/%d}/{s}.csv"
        if lake.meta(key) is None:
            continue
        obs = parse(lake.read(key))
        import pandas as pd
        df = pd.DataFrame([(s, o, v, desc, lag, now) for o, v in obs],
                          columns=["series", "obs_date", "value", "description", "release_lag_days", "observed_at"])
        con.execute("DELETE FROM macro_series WHERE series = ?", [s])
        con.register("df", df)
        con.execute("INSERT INTO macro_series SELECT * FROM df")
        con.unregister("df")
        out[s] = len(obs)
    return out
