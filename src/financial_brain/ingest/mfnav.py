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
from ..providers.amfi import AmfiNav, classify_scheme, parse_quality_report

SOURCE, DATASET = "AMFI", "mf_nav"
DEFAULT_STALE_AFTER_DAYS = 7
IST = ZoneInfo("Asia/Kolkata")


def ingest(
    con,
    cfg,
    business_date: date | None = None,
    *,
    provider=None,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
    known_scheme_codes: tuple[str, ...] = (),
) -> dict:
    """Fetch today's NAV file, preserve each row version, and report feed quality.

    The seven-day staleness threshold is an explicit operational default and may be
    overridden by a caller. Missing-scheme diagnostics require a caller-provided,
    point-in-time universe; no universe is inferred here.
    """
    if stale_after_days < 0:
        raise ValueError("stale_after_days cannot be negative")
    d = business_date or date.today()
    provider = provider or AmfiNav()
    res = provider.fetch(d)
    observed_at = res.retrieved_at
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("AMFI retrieved_at must be timezone-aware")
    observed_at = observed_at.astimezone(timezone.utc)
    source_published_at = res.extra.get("source_published_at")
    if source_published_at is not None:
        if (not isinstance(source_published_at, datetime)
                or source_published_at.tzinfo is None
                or source_published_at.utcoffset() is None):
            raise ValueError("source_published_at must be a timezone-aware datetime")
        source_published_at = source_published_at.astimezone(timezone.utc)

    report = parse_quality_report(
        res.payload,
        stale_after_days=stale_after_days,
        known_scheme_codes=known_scheme_codes,
    )
    rows = report["rows"]
    lake = RawLake(cfg.lake)
    obj = lake.put(source=SOURCE, dataset=DATASET, business_date=d,
                   filename=res.filename, payload=res.payload, url=res.url,
                   content_type=res.content_type, http_status=res.http_status,
                   retrieved_at=res.retrieved_at)
    # The source payload is now durably captured locally. This is the conservative
    # earliest point at which its row observations can become available to consumers.
    ingested_at = datetime.now(timezone.utc)
    con.execute("""INSERT INTO lake_manifest (key, source, dataset, business_date,
                   filename, url, retrieved_at, sha256, size_bytes, http_status,
                   content_type) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT (key) DO NOTHING""",
                [obj.key, obj.source, obj.dataset, obj.business_date, obj.filename,
                 obj.url, obj.retrieved_at, obj.sha256, obj.size_bytes, obj.http_status,
                 obj.content_type])
    observations = []
    for row_number, row in enumerate(rows, start=1):
        fields = {
            "scheme_code": row["scheme_code"], "isin_growth": row["isin_growth"],
            "isin_reinvest": row["isin_reinvest"], "scheme_name": row["scheme_name"],
            "fund_house": row["fund_house"], "scheme_type": row["scheme_type"],
            "plan": row["plan"], "option": row["option"], "nav": row["nav"],
            "nav_date": row["nav_date"].isoformat(),
        }
        row_digest = hashlib.sha256(
            json.dumps(fields, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        observation_id = hashlib.sha256(
            f"{obj.key}\0{row_number}\0{row['scheme_code']}\0{row['nav_date']}".encode("utf-8")
        ).hexdigest()
        observations.append((
            observation_id, row["scheme_code"], row["isin_growth"], row["isin_reinvest"],
            row["scheme_name"], row["fund_house"], row["scheme_type"], row["plan"],
            row["option"], row["nav"], row["nav_date"], obj.key, row_number,
            row_digest, source_published_at, observed_at, observed_at, ingested_at,
        ))

    con.executemany("""INSERT INTO mf_nav_observations (
        observation_id, scheme_code, isin_growth, isin_reinvest, scheme_name,
        fund_house, scheme_type, plan, option, nav, nav_date, lake_key, row_number,
        row_sha256, source_published_at, retrieved_at, observed_at, ingested_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (observation_id) DO NOTHING""",
        observations)
    # Keep the legacy table as the latest known projection. Historical/as-known queries
    # use mf_nav_observations, which is never overwritten by this update.
    con.executemany("""INSERT INTO mf_nav (scheme_code, isin_growth, isin_reinvest,
        scheme_name, fund_house, scheme_type, plan, option, nav, nav_date, lake_key,
        observed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT (scheme_code, nav_date) DO UPDATE SET
            isin_growth = excluded.isin_growth,
            isin_reinvest = excluded.isin_reinvest,
            scheme_name = excluded.scheme_name,
            fund_house = excluded.fund_house,
            scheme_type = excluded.scheme_type,
            plan = excluded.plan,
            option = excluded.option,
            nav = excluded.nav,
            lake_key = excluded.lake_key,
            observed_at = excluded.observed_at
        WHERE excluded.observed_at >= mf_nav.observed_at""",
        [[r["scheme_code"], r["isin_growth"], r["isin_reinvest"], r["scheme_name"],
          r["fund_house"], r["scheme_type"], r["plan"], r["option"], r["nav"],
          r["nav_date"], obj.key, observed_at] for r in rows])

    # Preserve point-in-time scheme descriptors as their own revisioned observation.
    # AMFI's NAV file does not provide effective dates, so observed_on is retrieval day;
    # classification falls back to UNCLASSIFIED whenever the descriptor is ambiguous.
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
                **classification, "observed_on": d.isoformat(),
                "observed_at": observed_at.isoformat(), "source_document_sha256": obj.sha256,
                "lake_key": obj.key}
        record_sha = hashlib.sha256(json.dumps(
            body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        master_rows.append([identity_key, row["scheme_code"], row["isin_growth"],
                            row["isin_reinvest"], row["scheme_name"], row["fund_house"],
                            row["scheme_type"], classification["plan_type"],
                            classification["payout_type"], classification["structure_type"],
                            classification["management_type"], classification["asset_class"],
                            row["plan"], row["option"], row["nav_date"], d, observed_at,
                            obj.sha256, record_sha, obj.key])
    con.executemany("""INSERT INTO mf_scheme_master_observations
        (identity_key, scheme_code, isin_growth, isin_reinvest, scheme_name, fund_house,
         scheme_type, plan_type, payout_type, structure_type, management_type, asset_class,
         plan, option, nav_as_of, observed_on, observed_at, source_document_sha256,
         record_sha256, lake_key)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT (identity_key, observed_on, source_document_sha256) DO NOTHING""",
        master_rows)

    latest = max((r["nav_date"] for r in rows), default=None)
    fresh = sum(1 for r in rows if r["nav_date"] == latest)
    quality = {
        "latest_nav_date": report["latest_nav_date"],
        "stale_after_days": report["stale_after_days"],
        "repeated_scheme_codes": report["repeated_scheme_codes"],
        "stale_scheme_codes": report["stale_scheme_codes"],
        "unavailable": report["unavailable"],
        "known_universe_supplied": bool(known_scheme_codes),
        "known_universe_size": len(set(known_scheme_codes)),
    }
    if latest:
        ledger.mint(con, kind="mf_nav_file", subject="AMFI", as_of=latest,
                    claim=f"AMFI NAV rows observed for {fresh} schemes as of {latest}",
                    value={"schemes": len(rows), "as_of_latest": fresh,
                           "latest_date": latest.isoformat(), "lake_key": obj.key,
                           "payload_sha256": obj.sha256,
                           "quality": quality},
                    source=SOURCE, source_tier=1, lake_key=obj.key,
                    derivation="providers/amfi NAVAll.txt")
    return {"schemes": len(rows), "latest_date": str(latest), "on_latest_date": fresh,
            "lake_key": obj.key, "observations_recorded": len(observations),
            "source_published_at": (source_published_at.isoformat()
                                     if source_published_at else None),
            "observed_at": observed_at.isoformat(),
            "ingested_at": ingested_at.isoformat(), "quality": quality}


def as_known_on(con, scheme_code: str, nav_date: date, known_at: datetime | None = None,
                *, same_day_nav_cutoff: datetime | None = None) -> dict | None:
    """Return the latest NAV observation locally available by ``known_at``.

    AMFI's NAVAll feed does not expose a per-row publication timestamp. In that case
    observed_at records provider retrieval; ingested_at records when this database
    persisted it. New observations use the latter as their availability bound. Older
    rows without it conservatively fall back to observed_at. A publication timestamp is
    preserved when an authorized provider supplies one, but never inferred from NAV or
    request date.
    """
    if known_at is None:
        raise ValueError("known_at must be timezone-aware")
    if known_at.tzinfo is None or known_at.utcoffset() is None:
        raise ValueError("known_at must be timezone-aware")
    decision_utc = known_at.astimezone(timezone.utc)
    if same_day_nav_cutoff is not None:
        if (same_day_nav_cutoff.tzinfo is None or same_day_nav_cutoff.utcoffset() is None
                or same_day_nav_cutoff.astimezone(IST).date() != nav_date):
            raise ValueError("same_day_nav_cutoff must be aware and fall on nav_date")
    columns = {row[1] for row in con.execute(
        "PRAGMA table_info('mf_nav_observations')").fetchall()}
    if not {"scheme_code", "nav_date", "nav", "observed_at"} <= columns:
        raise ValueError("mf_nav_observations is missing required as-known columns")
    # Daily NAVs are often fetched before the AMC's end-of-day publication is complete.
    # Schemas with a separately persisted ingestion clock can answer from that exact
    # availability evidence. Older observation tables lack that clock, so same-day
    # results require an explicit cutoff. If a caller supplies a cutoff, enforce it in
    # either schema.
    if decision_utc.astimezone(IST).date() == nav_date:
        if same_day_nav_cutoff is not None:
            if decision_utc < same_day_nav_cutoff.astimezone(timezone.utc):
                return None
        elif "ingested_at" not in columns:
            return None
    availability_fields = [name for name in ("ingested_at", "retrieved_at", "observed_at")
                           if name in columns]
    available_at = "COALESCE(" + ", ".join(availability_fields) + ")"
    ordering = [f"{name} DESC" for name in ("lake_key", "record_sha256",
                                              "source_document_sha256") if name in columns]
    order_sql = (", " + ", ".join(ordering)) if ordering else ""
    row = con.execute(f"""SELECT *, {available_at} AS available_at
        FROM mf_nav_observations
        WHERE scheme_code = ? AND nav_date = ? AND {available_at} <= ?
        ORDER BY {available_at} DESC{order_sql} LIMIT 1""",
        [scheme_code, nav_date, decision_utc]).fetchone()
    if row is None:
        return None
    names = [description[0] for description in con.description]
    return dict(zip(names, row, strict=True))


__all__ = ["as_known_on", "ingest"]
