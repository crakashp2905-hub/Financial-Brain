"""Fill index-close sessions NSE's archive does not hold, from Kite (Tier 2).

Only dates with prices but no index level are filled, only for indices Kite carries,
and only after Kite's history for that index is shown to agree with NSE's own levels
on the surrounding sessions. A name that maps to the wrong Kite instrument therefore
fails validation and writes nothing, instead of filling holes with a different series.
"""
from __future__ import annotations

import statistics
from datetime import date, datetime, timedelta, timezone

from ..lake.store import RawLake

#: Kite's close must match NSE's within this on the overlap before anything is written.
MAX_MEDIAN_DEVIATION = 0.001      # 0.1%
#: Sessions of overlap required either side of the gap for that validation.
MIN_OVERLAP = 20


def missing_index_dates(con) -> list[date]:
    return [r[0] for r in con.execute("""
        SELECT DISTINCT business_date FROM universe_snapshots
        EXCEPT SELECT DISTINCT business_date FROM index_levels ORDER BY 1""").fetchall()]


def fill(con, provider, lake: RawLake, *, indices: list[str] | None = None) -> dict:
    """Fill missing index dates. Returns a per-index report."""
    missing = missing_index_dates(con)
    if not missing:
        return {"missing_dates": 0, "indices": {}}

    lo, hi = min(missing) - timedelta(days=60), max(missing) + timedelta(days=60)
    nse_names = {r[0].upper(): r[0] for r in con.execute(
        "SELECT DISTINCT index_name FROM index_levels WHERE business_date BETWEEN ? AND ?",
        [lo, hi]).fetchall()}
    tokens = provider.index_tokens()
    wanted = [n.upper() for n in indices] if indices else sorted(nse_names)
    report, now = {}, datetime.now(timezone.utc)

    for name in wanted:
        if name not in tokens or name not in nse_names:
            report[name] = "not carried by Kite" if name not in tokens else "no NSE history"
            continue
        res = provider.fetch_range(tokens[name], lo, hi)
        obj = lake.put(source=provider.source, dataset=provider.dataset,
                       business_date=hi, filename=res.filename, payload=res.payload,
                       url=res.url, content_type=res.content_type,
                       http_status=res.http_status, retrieved_at=res.retrieved_at)
        con.execute(
            """INSERT INTO lake_manifest (key, source, dataset, business_date, filename, url,
               retrieved_at, sha256, size_bytes, http_status, content_type)
               VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (key) DO NOTHING""",
            [obj.key, obj.source, obj.dataset, obj.business_date, obj.filename, obj.url,
             obj.retrieved_at, obj.sha256, obj.size_bytes, obj.http_status, obj.content_type])
        rows = {r["business_date"]: r for r in provider.parse(res.payload)}

        nse = dict(con.execute(
            """SELECT business_date, close_level FROM index_levels
               WHERE index_name = ? AND business_date BETWEEN ? AND ?""",
            [nse_names[name], lo, hi]).fetchall())
        overlap = [abs(rows[d]["close_level"] / nse[d] - 1)
                   for d in nse if d in rows and nse[d]]
        if len(overlap) < MIN_OVERLAP:
            report[name] = f"refused: only {len(overlap)} overlapping sessions to validate"
            continue
        dev = statistics.median(overlap)
        if dev > MAX_MEDIAN_DEVIATION:
            report[name] = f"refused: median deviation vs NSE {dev:.3%} on {len(overlap)} days"
            continue

        written = 0
        for d in missing:
            r = rows.get(d)
            if not r:
                continue
            written += con.execute(
                """INSERT INTO index_levels (business_date, index_name, open_level,
                   high_level, low_level, close_level, variant, source, observed_at)
                   VALUES (?,?,?,?,?,?,'PRICE','KITE',?)
                   ON CONFLICT (business_date, index_name, variant) DO NOTHING
                   RETURNING 1""",
                [d, nse_names[name], r["open_level"], r["high_level"], r["low_level"],
                 r["close_level"], now]).fetchall().__len__()
        report[name] = (f"filled {written} of {len(missing)} missing dates "
                        f"(validated on {len(overlap)} days, median dev {dev:.4%})")
    return {"missing_dates": len(missing), "indices": report}
