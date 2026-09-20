"""Company ratios from Screener (Tier 3), cross-checked against our own prices.

Screener compiles what companies file; it is not the filer. So a number from here is
stored as a Tier-3 claim and, where we can compute the same thing from Tier-1 data, it
is *compared* rather than trusted: the close we hold for the same day against Screener's
"Current Price". A disagreement beyond ``TOLERANCE`` is recorded on the snapshot, which
is how a stale scrape, a wrong symbol mapping or a corporate action we have not applied
announces itself.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

from ..config import TIER
from ..evidence import ledger
from ..lake.store import RawLake
from ..providers.screener import Screener, parse_company
from .corpact_feed import _register

TOLERANCE = 0.02        # 2% between Screener's price and our own close


def _our_close(con, symbol: str) -> tuple[float, date] | None:
    row = con.execute("""SELECT close_price, business_date FROM eod_prices
                         WHERE ticker = ? AND close_price IS NOT NULL
                         ORDER BY business_date DESC LIMIT 1""", [symbol]).fetchone()
    return (float(row[0]), row[1]) if row else None


def fetch_company(con, cfg, symbol: str, *, provider=None, today: date | None = None) -> dict:
    provider = provider or Screener()
    lake, d = RawLake(cfg.lake), today or date.today()
    res = provider.fetch_company(symbol)
    obj = lake.put(source="SCREENER", dataset="company", business_date=d,
                   filename=res.filename, payload=res.payload, url=res.url,
                   content_type=res.content_type, http_status=res.http_status,
                   retrieved_at=res.retrieved_at)
    _register(con, obj)
    parsed = parse_company(res.payload)
    ratios = parsed["ratios"]

    check = None
    ours = _our_close(con, symbol.upper())
    theirs = (ratios.get("Current Price") or {}).get("value")
    if ours and theirs:
        drift = abs(theirs - ours[0]) / ours[0]
        check = {"our_close": ours[0], "our_date": ours[1].isoformat(),
                 "screener_price": theirs, "drift": round(drift, 4),
                 "agrees": drift <= TOLERANCE}

    con.execute("""INSERT INTO company_fundamentals (symbol, fetched_on, company_name,
                   ratios, price_check, lake_key, observed_at) VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT (symbol, fetched_on) DO NOTHING""",
                [symbol.upper(), d, parsed["name"], json.dumps(ratios),
                 json.dumps(check) if check else None, obj.key,
                 datetime.now(timezone.utc)])

    named = ", ".join(f"{k} {v['raw']}" for k, v in list(ratios.items())[:4])
    ledger.mint(con, kind="fundamentals", subject=symbol.upper(),
                as_of=datetime.combine(d, datetime.min.time()),
                claim=f"{parsed['name'] or symbol}: {named} (Screener)",
                value={"symbol": symbol.upper(), "ratios": ratios, "price_check": check},
                source="SCREENER", source_tier=TIER["SCREENER"], lake_key=obj.key,
                derivation="providers/screener top-ratios",
                confidence="medium" if not check or check["agrees"] else "low",
                quality="ok" if not check or check["agrees"] else "disputed")
    return {"symbol": symbol.upper(), "name": parsed["name"], "ratios": len(ratios),
            "price_check": check, "lake_key": obj.key}
