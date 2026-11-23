"""Archive and ingest normalized AMC mutual-fund portfolio disclosures."""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone

from ..config import Config, load
from ..lake.store import RawLake
from ..providers.mf_portfolio_disclosure import parse
from ..storage.db import Database


def ingest(payload: bytes, observed_on: date, cfg: Config | None = None) -> dict:
    cfg = cfg or load()
    db, lake = Database(cfg), RawLake(cfg.lake)
    db.migrate()
    rows = parse(payload, observed_on)
    imported_at = datetime.now(timezone.utc)
    digest = hashlib.sha256(payload).hexdigest()
    obj = lake.put(source="LOCAL_IMPORT", dataset="mf_portfolio_disclosures",
                   business_date=observed_on, filename=f"mf-portfolio-{digest[:12]}.csv",
                   payload=payload, url="local-import://official-amc-portfolio-disclosure-csv",
                   content_type="text/csv", retrieved_at=imported_at)
    with db.connect() as con:
        con.execute("""INSERT INTO lake_manifest
            (key, source, dataset, business_date, filename, url, retrieved_at, sha256,
             size_bytes, http_status, content_type) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (key) DO NOTHING""",
            [obj.key, obj.source, obj.dataset, obj.business_date, obj.filename, obj.url,
             obj.retrieved_at, obj.sha256, obj.size_bytes, obj.http_status, obj.content_type])
        con.executemany("""INSERT INTO mf_portfolio_disclosures
            (record_id, scheme_code, plan, option, scheme_isin, disclosure_date,
             asset_class, weight_percent, source_id, source_ref, source_sha256,
             published_at, observed_at, portfolio_as_of, instrument_id,
             underlying_scheme_code, holding_name, asset_type, sector,
             weight_pct, market_value_inr, quantity, credit_rating, maturity_date,
             modified_duration_years, yield_to_maturity_pct, observed_on,
             source_url, source_document_sha256, source_published_at, lake_key)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (scheme_code, portfolio_as_of, instrument_id, source_document_sha256)
            DO NOTHING""",
            [[hashlib.sha256((r["scheme_code"] + "|" + r["portfolio_as_of"].isoformat()
                              + "|" + r["instrument_id"] + "|"
                              + r["source_document_sha256"]).encode()).hexdigest(),
              r["scheme_code"], None, None, None, r["portfolio_as_of"],
              r["asset_type"], str(r["weight_pct"]) if r["weight_pct"] is not None else None,
              "LOCAL_IMPORT", r["source_url"], r["source_document_sha256"],
              r["source_published_at"], imported_at, r["portfolio_as_of"],
              r["instrument_id"],
              r["underlying_scheme_code"], r["holding_name"],
              r["asset_type"], r["sector"], r["weight_pct"], r["market_value_inr"], r["quantity"],
              r["credit_rating"], r["maturity_date"], r["modified_duration_years"],
              r["yield_to_maturity_pct"], observed_on, r["source_url"],
              r["source_document_sha256"], r["source_published_at"], obj.key] for r in rows])
    return {"rows": len(rows), "import_sha256": digest, "lake_key": obj.key}
