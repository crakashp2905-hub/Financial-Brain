"""Import an official AMFI NAV history export within AMFI's per-report date limit."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from urllib.parse import urlparse

from ..config import Config, load
from ..lake.store import RawLake
from ..providers.amfi import parse
from ..storage.db import Database

MAX_HISTORY_DAYS = 90
_AMFI_HOSTS = {"www.amfiindia.com", "amfiindia.com", "portal.amfiindia.com"}


def validate_request(start: date, end: date, source_url: str) -> None:
    if end < start:
        raise ValueError("history end date cannot precede start date")
    if (end - start).days + 1 > MAX_HISTORY_DAYS:
        raise ValueError("AMFI history export is limited to 90 calendar days per request")
    parsed = urlparse(source_url)
    if parsed.scheme != "https" or parsed.hostname not in _AMFI_HOSTS:
        raise ValueError("source_url must identify an official HTTPS AMFI page")


def ingest_export(payload: bytes, *, start: date, end: date, source_url: str,
                  cfg: Config | None = None) -> dict:
    """Archive and load one AMFI history export; never splits an over-limit date range.

    AMFI's history UI documents a maximum of 90 days per download:
    https://www.amfiindia.com/net-asset-value/nav-download
    """
    validate_request(start, end, source_url)
    rows = parse(payload)
    outside = [row["nav_date"] for row in rows
               if not start <= row["nav_date"] <= end]
    if outside:
        raise ValueError("AMFI export contains NAV dates outside the declared request window")
    cfg = cfg or load()
    db, lake = Database(cfg), RawLake(cfg.lake)
    db.migrate()
    imported_at = datetime.now(timezone.utc)
    digest = hashlib.sha256(payload).hexdigest()
    obj = lake.put(source="AMFI", dataset="mf_nav_history", business_date=end,
                   filename=f"NAVHistory-{start.isoformat()}-{end.isoformat()}-{digest[:12]}.txt",
                   payload=payload, url=source_url, content_type="text/plain",
                   retrieved_at=imported_at)
    with db.connect() as con:
        con.execute("""INSERT INTO lake_manifest
            (key, source, dataset, business_date, filename, url, retrieved_at,
             sha256, size_bytes, http_status, content_type)
            VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (key) DO NOTHING""",
            [obj.key, obj.source, obj.dataset, obj.business_date, obj.filename, obj.url,
             obj.retrieved_at, obj.sha256, obj.size_bytes, obj.http_status, obj.content_type])
        con.execute("BEGIN TRANSACTION")
        try:
            for row_number, row in enumerate(rows, start=1):
                record = {key: (str(value) if value is not None else None)
                          for key, value in row.items()}
                record["source_document_sha256"] = obj.sha256
                record_hash = hashlib.sha256(json.dumps(
                    record, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                con.execute("""INSERT INTO mf_nav
                    (scheme_code, isin_growth, isin_reinvest, scheme_name, fund_house,
                     scheme_type, plan, option, nav, nav_date, lake_key, observed_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT (scheme_code, nav_date) DO UPDATE SET
                        isin_growth=EXCLUDED.isin_growth, isin_reinvest=EXCLUDED.isin_reinvest,
                        scheme_name=EXCLUDED.scheme_name, fund_house=EXCLUDED.fund_house,
                        scheme_type=EXCLUDED.scheme_type, plan=EXCLUDED.plan,
                        option=EXCLUDED.option, nav=EXCLUDED.nav, lake_key=EXCLUDED.lake_key,
                        observed_at=EXCLUDED.observed_at""",
                    [row["scheme_code"], row["isin_growth"], row["isin_reinvest"],
                     row["scheme_name"], row["fund_house"], row["scheme_type"],
                     row["plan"], row["option"], row["nav"], row["nav_date"], obj.key,
                     imported_at])
                observation_id = hashlib.sha256(
                    f"{obj.sha256}|{row['scheme_code']}|{row['nav_date']}|{row_number}".encode()
                ).hexdigest()
                con.execute("""INSERT INTO mf_nav_observations
                    (observation_id, scheme_code, nav_date, nav, isin_growth, isin_reinvest,
                     scheme_name, fund_house, scheme_type, plan, option, lake_key,
                     row_number, row_sha256, source_document_sha256, source_published_at,
                     retrieved_at, observed_at, ingested_at, record_sha256)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT (scheme_code, nav_date, source_document_sha256) DO NOTHING""",
                    [observation_id, row["scheme_code"], row["nav_date"], row["nav"],
                     row["isin_growth"], row["isin_reinvest"], row["scheme_name"],
                     row["fund_house"], row["scheme_type"], row["plan"], row["option"],
                     obj.key, row_number, record_hash, obj.sha256, None, imported_at,
                     imported_at, imported_at, record_hash])
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return {"rows": len(rows), "start": start.isoformat(), "end": end.isoformat(),
            "max_days_per_export": MAX_HISTORY_DAYS, "source_url": source_url,
            "source_document_sha256": obj.sha256, "lake_key": obj.key}
