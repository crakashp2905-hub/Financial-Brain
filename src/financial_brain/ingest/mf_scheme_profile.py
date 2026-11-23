"""Import source-attributed AMC/SID scheme profile facts without backdating observations."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

from ..config import Config, load
from ..lake.store import RawLake
from ..providers.amfi_scheme_profile import parse
from ..storage.db import Database


def ingest(payload: bytes, observed_on: date, cfg: Config | None = None) -> dict:
    cfg = cfg or load()
    db, lake = Database(cfg), RawLake(cfg.lake)
    db.migrate()
    rows = parse(payload, observed_on)
    imported_at = datetime.now(timezone.utc)
    import_hash = hashlib.sha256(payload).hexdigest()
    obj = lake.put(source="LOCAL_IMPORT", dataset="mf_scheme_profiles",
                   business_date=observed_on,
                   filename=f"mf-scheme-profiles-{import_hash[:12]}.csv",
                   payload=payload, url="local-import://official-amc-sid-scheme-profile-csv",
                   content_type="text/csv", retrieved_at=imported_at)
    with db.connect() as con:
        con.execute("""INSERT INTO lake_manifest
            (key, source, dataset, business_date, filename, url, retrieved_at,
             sha256, size_bytes, http_status, content_type)
            VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (key) DO NOTHING""",
            [obj.key, obj.source, obj.dataset, obj.business_date, obj.filename, obj.url,
             obj.retrieved_at, obj.sha256, obj.size_bytes, obj.http_status, obj.content_type])
        con.execute("BEGIN TRANSACTION")
        try:
            for row in rows:
                body = {**row, "observed_on": observed_on.isoformat(),
                        "observed_at": imported_at.isoformat(), "lake_key": obj.key}
                record_hash = hashlib.sha256(json.dumps(
                    {k: (str(v) if v is not None else None) for k, v in body.items()},
                    sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                con.execute("""INSERT INTO mf_scheme_profile_disclosures
                    (identity_key, scheme_code, plan, option, isin, riskometer, benchmark_name,
                     benchmark_return_type, manager, objective, effective_from, effective_to,
                     scheme_status, status_effective_from, successor_scheme_code, status_reason,
                     observed_on, observed_at, source_url, source_document_sha256,
                     source_published_at, import_sha256, record_sha256, lake_key)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT (identity_key, observed_on, source_document_sha256) DO NOTHING""",
                    [row["identity_key"], row["scheme_code"], row["plan"], row["option"],
                     row["isin"], row["riskometer"], row["benchmark_name"],
                     row["benchmark_return_type"], row["manager"], row["objective"],
                     row["effective_from"], row["effective_to"], row["scheme_status"],
                     row["status_effective_from"], row["successor_scheme_code"],
                     row["status_reason"], observed_on, imported_at,
                     row["source_url"], row["source_document_sha256"],
                     row["source_published_at"], import_hash, record_hash, obj.key])
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return {"rows": len(rows), "import_sha256": import_hash, "lake_key": obj.key}
