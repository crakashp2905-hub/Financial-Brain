"""Daily FII/FPI and DII cash-market flows from NSE (``/api/fiidiiTradeReact``): buy, sell and net, in Rs crore.

The endpoint returns only the latest session, so this is a forward record from 2026-10-18, collected nightly; each
response is kept in the raw lake.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

from ..lake.store import RawLake

API = "https://www.nseindia.com/api/fiidiiTradeReact"


def _f(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse(payload: bytes) -> list[tuple[date, str, float | None, float | None, float | None]]:
    out = []
    for r in json.loads(payload):
        try:
            d = datetime.strptime(r["date"], "%d-%b-%Y").date()
        except (KeyError, ValueError):
            continue
        out.append((d, r.get("category"), _f(r.get("buyValue")), _f(r.get("sellValue")), _f(r.get("netValue"))))
    return out


def collect(con, cfg, *, provider=None) -> dict:
    from ..providers.nse_results import NSEResults
    provider = provider or NSEResults(min_interval=1)
    body, _, _ = provider._get(API)
    rows = parse(body)
    if not rows:
        return {"rows": 0}
    RawLake(cfg.lake).put(source="NSE", dataset="fii_dii", business_date=rows[0][0], filename="fiidii.json",
                          payload=body, url=API, content_type="application/json")
    now = datetime.now(timezone.utc)
    con.executemany("INSERT OR REPLACE INTO fii_dii_flows VALUES (?,?,?,?,?,?)", [[*r, now] for r in rows])
    return {"rows": len(rows), "date": str(rows[0][0])}
