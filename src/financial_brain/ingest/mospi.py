"""Official CPI and IIP from MoSPI's open endpoints (``api.mospi.gov.in``), with a point-in-time availability date.

    CPI   /api/cpi/getCPIIndex?base_year=2012&year=Y&state_code=99&group_code=0   All-India general index and inflation,
          rural / urban / combined, status P(rovisional) or F(inal). Base 2012 runs to December 2025; the 2024-base
          series from January 2026 is not served by this endpoint (a stated gap).
    IIP   /api/iip/getIIPMonthly?year=Y&month_code=M   general, sectoral and use-based indices and growth, both bases
          (2011-12 and 2022-23).

These endpoints answer without an account (the rest of the API needs registration, which this system never does).
MoSPI gives no release date in the data, so ``available_on`` is set conservatively: CPI on the 13th of the following
month (released on the 12th), IIP on the 13th of the month after next (released on the 12th of month + 2 until 2024,
then the 28th of month + 1 - the later rule covers both). A signal must use ``available_on``, never the period.
"""
from __future__ import annotations

import json
import urllib.request
from datetime import date, datetime, timezone

from ..lake.store import RawLake

BASE = "https://api.mospi.gov.in/api"
UA = "Mozilla/5.0 (financial-brain personal research)"
MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August",
                                       "September", "October", "November", "December"], 1)}


def _context():
    """MoSPI's server still uses legacy TLS renegotiation, which Python refuses by default. Allow it for this host only;
    certificates are still verified."""
    import ssl
    ctx = ssl.create_default_context()
    ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
    return ctx


def _get(path: str) -> bytes:
    req = urllib.request.Request(f"{BASE}/{path}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90, context=_context()) as r:
        return r.read()


def _add_months(d: date, n: int, day: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, day)


def available_on(series: str, period: date) -> date:
    return _add_months(period, 1, 13) if series == "CPI" else _add_months(period, 2, 13)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse(series: str, payload: bytes) -> list[tuple]:
    out = []
    for r in json.loads(payload).get("data") or []:
        m = MONTHS.get(str(r.get("month")))
        if not m:
            continue
        period = date(int(r["year"]), m, 1)
        if series == "CPI":
            base, cat, sub = str(r.get("baseyear")), str(r.get("sector")), str(r.get("subgroup"))
            val, growth, status = _f(r.get("index")), _f(r.get("inflation")), r.get("status")
        else:
            base, cat, sub = str(r.get("base_year")), str(r.get("type")), f"{r.get('category')}|{r.get('sub_category') or ''}"
            val, growth, status = _f(r.get("index")), _f(r.get("growth_rate")), None
        if val is None:
            continue
        out.append((series, period, base, cat, sub, val, growth, status, available_on(series, period)))
    return out


def prefetch(cfg, start_year: int = 2013, end_year: int | None = None, *, get=_get) -> dict:
    lake = RawLake(cfg.lake)
    end_year = end_year or date.today().year
    out = {"cpi": 0, "iip": 0, "failed": []}
    for y in range(start_year, end_year + 1):
        for m in range(1, 13):
            if date(y, m, 1) > date.today():
                break
            if y <= 2025:                               # base-2012 CPI ends in December 2025
                try:
                    body = get(f"cpi/getCPIIndex?base_year=2012&year={y}&month_code={m}&state_code=99&group_code=0&format=json")
                    lake.put(source="MOSPI", dataset="cpi", business_date=date(y, m, 1), filename=f"cpi_{y}_{m:02d}.json",
                             payload=body, url=f"{BASE}/cpi/getCPIIndex?year={y}&month_code={m}", content_type="application/json")
                    out["cpi"] += 1
                except OSError as e:
                    out["failed"].append(f"cpi {y}-{m}: {e}")
            try:
                body = get(f"iip/getIIPMonthly?year={y}&month_code={m}&format=json")
                lake.put(source="MOSPI", dataset="iip", business_date=date(y, m, 1), filename=f"iip_{y}_{m:02d}.json",
                         payload=body, url=f"{BASE}/iip/getIIPMonthly?year={y}&month_code={m}", content_type="application/json")
                out["iip"] += 1
            except OSError as e:
                out["failed"].append(f"iip {y}-{m}: {e}")
    return out


def load(con, cfg) -> dict:
    import pandas as pd
    lake = RawLake(cfg.lake)
    rows = []
    for ds, series in (("cpi", "CPI"), ("iip", "IIP")):
        for obj in lake.iter_objects("MOSPI", ds):
            rows += parse(series, lake.read(obj.key))
    if not rows:
        return {"rows": 0}
    df = pd.DataFrame(rows, columns=["series", "period", "base_year", "category", "subcategory", "value", "growth",
                                     "status", "available_on"])
    df["observed_at"] = datetime.now(timezone.utc)
    df = df.drop_duplicates(["series", "period", "base_year", "category", "subcategory"], keep="last")
    con.register("df", df)
    con.execute("INSERT OR REPLACE INTO official_macro SELECT * FROM df")
    con.unregister("df")
    return {"rows": len(df), "series": df.groupby("series").size().to_dict()}
