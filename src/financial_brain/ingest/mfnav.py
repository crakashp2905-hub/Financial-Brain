"""Ingest AMFI's daily NAV feed (Tier 1) into ``mf_nav``.

The file is stored in the lake first, then parsed - so what we believed on any day can
be replayed from the bytes, and a re-run of the same file is a no-op rather than a
duplicate. Rows are keyed by the date each row carries: AMFI's file mixes today's NAVs
with stale ones for schemes that stopped reporting, and a scheme that has not declared
a NAV since 2018 must not be filed under today.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from ..evidence import ledger
from ..lake.store import RawLake
from ..providers.amfi import AmfiNav, classify_scheme, parse

SOURCE, DATASET = "AMFI", "mf_nav"


def ingest(con, cfg, business_date: date | None = None, *, provider=None) -> dict:
    """Fetch (or re-read) today's NAV file and upsert its rows."""
    d = business_date or date.today()
    provider = provider or AmfiNav()
    lake = RawLake(cfg.lake)
    res = provider.fetch(d)
    obj = lake.put(source=SOURCE, dataset=DATASET, business_date=d,
                   filename=res.filename, payload=res.payload, url=res.url,
                   content_type=res.content_type, http_status=res.http_status,
                   retrieved_at=res.retrieved_at)
    con.execute("""INSERT INTO lake_manifest (key, source, dataset, business_date,
                   filename, url, retrieved_at, sha256, size_bytes, http_status,
                   content_type) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT (key) DO NOTHING""",
                [obj.key, obj.source, obj.dataset, obj.business_date, obj.filename,
                 obj.url, obj.retrieved_at, obj.sha256, obj.size_bytes, obj.http_status,
                 obj.content_type])

    rows = parse(res.payload)
    now = datetime.now(timezone.utc)
    con.executemany("""INSERT INTO mf_nav (scheme_code, isin_growth, isin_reinvest,
        scheme_name, fund_house, scheme_type, plan, option, nav, nav_date, lake_key,
        observed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT (scheme_code, nav_date) DO UPDATE SET
            isin_growth=EXCLUDED.isin_growth, isin_reinvest=EXCLUDED.isin_reinvest,
            scheme_name=EXCLUDED.scheme_name, fund_house=EXCLUDED.fund_house,
            scheme_type=EXCLUDED.scheme_type, plan=EXCLUDED.plan, option=EXCLUDED.option,
            nav=EXCLUDED.nav, lake_key=EXCLUDED.lake_key, observed_at=EXCLUDED.observed_at""",
        [[r["scheme_code"], r["isin_growth"], r["isin_reinvest"], r["scheme_name"],
          r["fund_house"], r["scheme_type"], r["plan"], r["option"], r["nav"],
          r["nav_date"], obj.key, now] for r in rows])

    revision_rows = []
    for row in rows:
        revision = {key: row[key] for key in
                    ("scheme_code", "isin_growth", "isin_reinvest", "scheme_name",
                     "fund_house", "scheme_type", "plan", "option", "nav", "nav_date")}
        revision.update({"lake_key": obj.key, "source_document_sha256": obj.sha256,
                         "source_published_at": None,
                         "retrieved_at": res.retrieved_at, "observed_at": now})
        record_hash = hashlib.sha256(json.dumps(
            {key: str(value) if value is not None else None
             for key, value in revision.items()}, sort_keys=True,
            separators=(",", ":")).encode("utf-8")).hexdigest()
        revision_rows.append([row["scheme_code"], row["nav_date"], row["nav"],
                              row["isin_growth"], row["isin_reinvest"], row["scheme_name"],
                              row["fund_house"], row["scheme_type"], row["plan"], row["option"],
                              obj.key, obj.sha256, None, res.retrieved_at, now, record_hash])
    con.executemany("""INSERT INTO mf_nav_observations
        (scheme_code, nav_date, nav, isin_growth, isin_reinvest, scheme_name, fund_house,
         scheme_type, plan, option, lake_key, source_document_sha256, source_published_at,
         retrieved_at, observed_at, record_sha256)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT (scheme_code, nav_date, source_document_sha256) DO NOTHING""",
        revision_rows)

    master_rows = []
    for row in rows:
        classification = classify_scheme(row)
        identity = "|".join((row["scheme_code"], row["plan"] or "", row["option"] or "",
                             row["isin_growth"] or "", row["isin_reinvest"] or ""))
        identity_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        body = {"identity_key": identity_key, "scheme_code": row["scheme_code"],
                "isin_growth": row["isin_growth"], "isin_reinvest": row["isin_reinvest"],
                "scheme_name": row["scheme_name"], "fund_house": row["fund_house"],
                "scheme_type": row["scheme_type"], "plan": row["plan"],
                "option": row["option"], "nav_as_of": row["nav_date"].isoformat(),
                **classification,
                "observed_on": d.isoformat(), "observed_at": now.isoformat(),
                "source_document_sha256": obj.sha256, "lake_key": obj.key}
        record_sha = hashlib.sha256(json.dumps(
            body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        master_rows.append([identity_key, row["scheme_code"], row["isin_growth"],
                            row["isin_reinvest"], row["scheme_name"], row["fund_house"],
                            row["scheme_type"], classification["plan_type"],
                            classification["payout_type"], classification["structure_type"],
                            classification["management_type"], classification["asset_class"],
                            row["plan"], row["option"], row["nav_date"],
                            d, now, obj.sha256, record_sha, obj.key])
    con.executemany("""INSERT INTO mf_scheme_master_observations
        (identity_key, scheme_code, isin_growth, isin_reinvest, scheme_name, fund_house,
         scheme_type, plan_type, payout_type, structure_type, management_type, asset_class,
         plan, option, nav_as_of, observed_on, observed_at,
         source_document_sha256, record_sha256, lake_key)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT (identity_key, observed_on, source_document_sha256) DO NOTHING""",
        master_rows)

    latest = max((r["nav_date"] for r in rows), default=None)
    fresh = sum(1 for r in rows if r["nav_date"] == latest)
    if latest:
        ledger.mint(con, kind="mf_nav_file", subject="AMFI", as_of=latest,
                    claim=f"AMFI published NAVs for {fresh} schemes as of {latest}",
                    value={"schemes": len(rows), "as_of_latest": fresh,
                           "latest_date": latest.isoformat(),
                           "source_document_sha256": obj.sha256},
                    source=SOURCE, source_tier=1, lake_key=obj.key,
                    derivation="providers/amfi NAVAll.txt")
    return {"schemes": len(rows), "latest_date": str(latest), "on_latest_date": fresh,
            "lake_key": obj.key}


def as_known_on(con, scheme_code: str, nav_date: date, decision_time: datetime, *,
                same_day_nav_cutoff: datetime | None = None) -> dict | None:
    """Return only NAV known by decision time; same-day rows need publication evidence.

    When the source supplies no publication timestamp, same-day use is refused unless the
    caller supplies an explicit timezone-aware cutoff for that NAV date.
    """
    if decision_time.tzinfo is None or decision_time.utcoffset() is None:
        raise ValueError("decision_time must be timezone-aware")
    if same_day_nav_cutoff is not None:
        if (same_day_nav_cutoff.tzinfo is None or same_day_nav_cutoff.utcoffset() is None
                or same_day_nav_cutoff.astimezone(ZoneInfo("Asia/Kolkata")).date() != nav_date):
            raise ValueError("same_day_nav_cutoff must be aware and fall on nav_date")
    row = con.execute("""SELECT nav, source_document_sha256, source_published_at,
                               retrieved_at, observed_at, lake_key, record_sha256
                        FROM mf_nav_observations
                        WHERE scheme_code=? AND nav_date=? AND observed_at<=?
                          AND (source_published_at IS NULL OR source_published_at<=?)
                        ORDER BY observed_at DESC, source_document_sha256 DESC LIMIT 1""",
                      [scheme_code, nav_date, decision_time, decision_time]).fetchone()
    if row is None:
        return None
    local_decision_date = decision_time.astimezone(ZoneInfo("Asia/Kolkata")).date()
    if nav_date == local_decision_date and row[2] is None:
        if same_day_nav_cutoff is None or decision_time < same_day_nav_cutoff:
            return None
    keys = ("nav", "source_document_sha256", "source_published_at", "retrieved_at",
            "observed_at", "lake_key", "record_sha256")
    return dict(zip(keys, row))
