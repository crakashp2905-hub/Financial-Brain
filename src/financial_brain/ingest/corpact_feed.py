"""Ingest BSE's corporate-action feed (Tier 1) and reconcile it with derived actions.

Phase 0 had no authoritative corporate-action source, so actions were *derived* from
prices. This feed is the authority: reported actions are recorded with
``confidence='reported'`` and no ``derived_factor``, which also means
``detect.clear_derived`` can never delete them.

Scrip code -> ISIN is resolved **as of the ex-date**. That matters exactly here: a split's
ex-date is the day the ISIN changes, so the scrip maps to the listing whose span covers
the ex-date, or failing that (ex-date not a session) the nearest listing within
``MAX_GAP_DAYS``, preferring the ISIN that starts on or after the ex-date.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from ..config import TIER
from ..corpactions import actions as ca
from ..lake.store import RawLake
from ..providers.bse_corpact import BSECorporateActionsProvider

MAX_GAP_DAYS = 5


def _months(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _lake_month(lake: RawLake, cfg_lake, y: int, m: int):
    d = cfg_lake / "BSE" / "corporate_actions" / f"{y}" / f"{m:02d}"
    if not d.exists():
        return None
    metas = sorted(d.glob("*/*.meta.json"))
    if not metas:
        return None
    obj = lake.meta(metas[-1].relative_to(cfg_lake).as_posix()[: -len(".meta.json")])
    return (lake.read(obj), obj) if obj else None


def _register(con, obj) -> None:
    con.execute(
        """INSERT INTO lake_manifest (key, source, dataset, business_date, filename, url,
           retrieved_at, sha256, size_bytes, http_status, content_type)
           VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (key) DO NOTHING""",
        [obj.key, obj.source, obj.dataset, obj.business_date, obj.filename, obj.url,
         obj.retrieved_at, obj.sha256, obj.size_bytes, obj.http_status, obj.content_type])


def ingest(con, cfg, start: date, end: date, *, provider=None, today: date | None = None) -> dict:
    """Fetch (lake-first) every month in range, map to ISIN, record reported actions."""
    provider = provider or BSECorporateActionsProvider()
    lake, today = RawLake(cfg.lake), today or date.today()
    now = datetime.now(timezone.utc)
    stats = {"months": 0, "fetched": 0, "rows": 0, "recorded": 0, "unmapped": 0,
             "no_ex_date": 0}

    con.execute("""CREATE OR REPLACE TEMP TABLE _feed (scrip VARCHAR, ex_date DATE,
        record_date DATE, action_type VARCHAR, ratio_from DOUBLE, ratio_to DOUBLE,
        amount DOUBLE, purpose VARCHAR, evidence_key VARCHAR)""")

    for y, m in _months(start, end):
        stats["months"] += 1
        current = (y, m) >= (today.year, today.month)
        held = None if current else _lake_month(lake, cfg.lake, y, m)
        if held:
            payload, obj = held
        else:
            res = provider.fetch_month(y, m)
            last = date(y, m, 28)
            obj = lake.put(source=provider.source, dataset=provider.dataset,
                           business_date=last, filename=res.filename, payload=res.payload,
                           url=res.url, content_type=res.content_type,
                           http_status=res.http_status, retrieved_at=res.retrieved_at)
            payload = res.payload
            stats["fetched"] += 1
        _register(con, obj)
        rows = provider.parse(payload)
        stats["rows"] += len(rows)
        batch = []
        for a in rows:
            if not a.ex_date:
                stats["no_ex_date"] += 1
                continue
            batch.append([a.scrip_code, a.ex_date, a.record_date, a.action_type,
                          a.ratio_from, a.ratio_to, a.amount, a.purpose, obj.key])
        if batch:
            con.executemany("INSERT INTO _feed VALUES (?,?,?,?,?,?,?,?,?)", batch)

    mapped = con.execute(f"""
        WITH spans AS (
            SELECT instrument_id AS scrip, isin, MIN(first_seen) f, MAX(last_seen) l
            FROM security_listings WHERE exchange = 'BSE' AND instrument_id <> ''
            GROUP BY 1, 2
        ), cand AS (
            SELECT x.*, s.isin,
                   CASE WHEN x.ex_date BETWEEN s.f AND s.l THEN 0
                        WHEN s.f >= x.ex_date THEN date_diff('day', x.ex_date, s.f)
                        ELSE date_diff('day', s.l, x.ex_date) + 0.5 END AS dist
            FROM _feed x JOIN spans s ON s.scrip = x.scrip
        )
        SELECT scrip, ex_date, record_date, action_type, ratio_from, ratio_to, amount,
               purpose, evidence_key, isin
        FROM cand WHERE dist <= {MAX_GAP_DAYS}
        QUALIFY ROW_NUMBER() OVER (PARTITION BY scrip, ex_date, purpose ORDER BY dist) = 1
    """).fetchall()
    total = con.execute("SELECT COUNT(*) FROM (SELECT DISTINCT scrip, ex_date, purpose "
                        "FROM _feed)").fetchone()[0]
    stats["unmapped"] = total - len(mapped)

    for (scrip, ex_date, record_date, kind, rf, rt, amt, purpose, ev, isin) in mapped:
        detail = f"BSE feed: {purpose} (scrip {scrip})"
        aid = ca.action_id(isin, kind, ex_date, detail)
        n = con.execute(
            """INSERT INTO corporate_actions
               (action_id, isin, exchange, action_type, ex_date, record_date, ratio_from,
                ratio_to, amount, details, source, source_tier, observed_at,
                evidence_key, confidence)
               VALUES (?,?,'BSE',?,?,?,?,?,?,?,'BSE',?,?,?,'reported')
               ON CONFLICT (action_id) DO NOTHING RETURNING 1""",
            [aid, isin, kind, ex_date, record_date, rf, rt, amt, detail, TIER["BSE"], now,
             ev]).fetchall()
        stats["recorded"] += len(n)
    return stats


def reconcile(con, *, window_days: int = 3, factor_tol: float = 0.02) -> dict:
    """How accurate was price-based derivation? Compare it with what BSE reported.

    Precision - of derived split/bonus actions, how many match a reported one (same
    ISIN, ex-date within ``window_days``, factor within ``factor_tol``).
    Recall    - of reported split/bonus actions on securities that traded liquidly on
    the ex-date, how many derivation found.
    """
    derived = con.execute("""
        SELECT d.isin, d.ex_date, d.derived_factor,
               (SELECT MIN(ABS(r.ratio_from / r.ratio_to / d.derived_factor - 1))
                FROM corporate_actions r
                WHERE r.confidence = 'reported' AND r.ratio_to IS NOT NULL
                  AND r.isin = d.isin
                  AND ABS(date_diff('day', r.ex_date, d.ex_date)) <= ?) AS err,
               (SELECT COUNT(*) FROM corporate_actions r
                WHERE r.confidence = 'reported' AND r.isin = d.isin
                  AND ABS(date_diff('day', r.ex_date, d.ex_date)) <= ?) AS any_reported
        FROM corporate_actions d WHERE d.derived_factor IS NOT NULL
    """, [window_days, window_days]).fetchall()
    matched = sum(1 for *_, err, _n in derived if err is not None and err <= factor_tol)
    wrong_ratio = sum(1 for *_, err, _n in derived if err is not None and err > factor_tol)
    unreported = sum(1 for *_, err, n in derived if err is None)

    reported = con.execute("""
        SELECT r.isin, r.ex_date,
               EXISTS (SELECT 1 FROM corporate_actions d WHERE d.derived_factor IS NOT NULL
                       AND d.isin = r.isin
                       AND ABS(date_diff('day', d.ex_date, r.ex_date)) <= ?) AS found
        FROM corporate_actions r
        WHERE r.confidence = 'reported' AND r.action_type IN ('SPLIT', 'BONUS')
          AND r.ratio_to IS NOT NULL
          AND EXISTS (SELECT 1 FROM universe_snapshots u WHERE u.isin = r.isin
                      AND u.business_date = r.ex_date AND u.turnover >= 1e7)
    """, [window_days]).fetchall()
    found = sum(1 for *_, f in reported if f)
    return {
        "derived": len(derived), "derived_matched": matched,
        "derived_wrong_ratio": wrong_ratio, "derived_unreported": unreported,
        "precision": matched / len(derived) if derived else None,
        "reported_liquid": len(reported), "reported_found": found,
        "recall": found / len(reported) if reported else None,
    }
