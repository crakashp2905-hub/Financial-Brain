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

from ..evidence import ledger
from ..lake.store import RawLake
from ..providers.amfi import AmfiNav, parse

SOURCE, DATASET = "AMFI", "mf_nav"


def ingest(con, cfg, business_date: date | None = None, *, provider=None) -> dict:
    """Fetch (or re-read) today's NAV file and preserve each observed row version."""
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

    rows = parse(res.payload)
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
            row_digest, source_published_at, observed_at,
        ))

    con.executemany("""INSERT INTO mf_nav_observations (
        observation_id, scheme_code, isin_growth, isin_reinvest, scheme_name,
        fund_house, scheme_type, plan, option, nav, nav_date, lake_key, row_number,
        row_sha256, source_published_at, observed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (observation_id) DO NOTHING""",
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

    latest = max((r["nav_date"] for r in rows), default=None)
    fresh = sum(1 for r in rows if r["nav_date"] == latest)
    if latest:
        ledger.mint(con, kind="mf_nav_file", subject="AMFI", as_of=latest,
                    claim=f"AMFI NAV rows observed for {fresh} schemes as of {latest}",
                    value={"schemes": len(rows), "as_of_latest": fresh,
                           "latest_date": latest.isoformat(), "lake_key": obj.key,
                           "payload_sha256": obj.sha256},
                    source=SOURCE, source_tier=1, lake_key=obj.key,
                    derivation="providers/amfi NAVAll.txt")
    return {"schemes": len(rows), "latest_date": str(latest), "on_latest_date": fresh,
            "lake_key": obj.key, "observations_recorded": len(observations),
            "source_published_at": (source_published_at.isoformat()
                                     if source_published_at else None),
            "observed_at": observed_at.isoformat()}


def as_known_on(con, scheme_code: str, nav_date: date, *, known_at: datetime) -> dict | None:
    """Return the latest NAV observation that had been retrieved by ``known_at``.

    AMFI's NAVAll feed does not expose a per-row publication timestamp. In that case
    observed_at is the earliest defensible strategy-availability bound. A publication
    timestamp is preserved when an authorized provider supplies one, but is never
    inferred from nav_date or the request date.
    """
    if known_at.tzinfo is None or known_at.utcoffset() is None:
        raise ValueError("known_at must be timezone-aware")
    row = con.execute("""SELECT observation_id, scheme_code, nav, nav_date, lake_key,
        row_number, row_sha256, source_published_at, observed_at
        FROM mf_nav_observations
        WHERE scheme_code = ? AND nav_date = ? AND observed_at <= ?
        ORDER BY observed_at DESC, lake_key DESC, row_number DESC LIMIT 1""",
        [scheme_code, nav_date, known_at.astimezone(timezone.utc)]).fetchone()
    if row is None:
        return None
    keys = ("observation_id", "scheme_code", "nav", "nav_date", "lake_key",
            "row_number", "row_sha256", "source_published_at", "observed_at")
    return dict(zip(keys, row, strict=True))


__all__ = ["as_known_on", "ingest"]
