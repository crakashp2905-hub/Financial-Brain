"""Idempotent, replayable ingestion jobs with lineage and quarantine.

Each run does exactly this, in order:

    fetch -> land in the immutable lake -> record provenance -> parse -> quality gate
          -> curated Parquet -> security master -> universe snapshot -> record the run

Properties that matter:

* **Idempotent** - re-running a date that already succeeded is a no-op.
* **Replayable** - ``--from-lake`` rebuilds curated tables from stored bytes, no network.
* **Quarantining** - a payload failing an error-severity contract never reaches curated.
* **Lineage** - every curated row traces to a lake key, and every run is recorded.

One database connection is held for the whole run. DuckDB is single-writer, and opening a
connection per step produced intermittent lock failures on Windows.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from ..config import Config, load
from ..lake.store import LakeObject, RawLake
from ..providers.base import FetchError, NotPublished, Provider
from ..providers.bhavcopy import PROVIDERS
from ..securities import master as sec_master
from ..storage.db import Database
from ..universe import snapshot as universe
from . import quality

STATUS_OK = "ok"
STATUS_SKIPPED = "skipped"
STATUS_NOT_PUBLISHED = "not_published"
STATUS_PARTIAL = "ok_partial"
STATUS_QUARANTINED = "quarantined"
STATUS_FAILED = "failed"

TERMINAL_OK = (STATUS_OK, STATUS_PARTIAL, STATUS_SKIPPED, STATUS_NOT_PUBLISHED)


@dataclass
class RunResult:
    run_id: str
    status: str
    business_date: date
    source: str
    rows_in: int = 0
    rows_out: int = 0
    rows_rejected: int = 0
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status in TERMINAL_OK

    def __str__(self) -> str:
        rej = f" rejected={self.rows_rejected}" if self.rows_rejected else ""
        return (f"{self.business_date} {self.source:<4} {self.status:<14} "
                f"rows={self.rows_out:<6}{rej} {self.message}")


class BhavcopyIngestJob:
    """Ingest one exchange's cash-market bhavcopy for one business date."""

    job = "bhavcopy_ingest"

    def __init__(self, source: str, cfg: Config | None = None):
        if source not in PROVIDERS:
            raise ValueError(f"unknown source {source!r}; known: {sorted(PROVIDERS)}")
        self.cfg = cfg or load()
        self.provider: Provider = PROVIDERS[source]()
        self.lake = RawLake(self.cfg.lake)
        self.db = Database(self.cfg)

    # ------------------------------------------------------------------ run
    def run(self, business_date: date, *, force: bool = False, from_lake: bool = False) -> RunResult:
        with self.db.connect() as con:
            return self._run(con, business_date, force=force, from_lake=from_lake)

    def _run(self, con, business_date: date, *, force: bool, from_lake: bool) -> RunResult:
        run_id = uuid.uuid4().hex[:16]
        started = datetime.now(timezone.utc)
        src = self.provider.source

        # Replaying from the lake still skips days already published. Re-processing a
        # day after a parser change is what --force is for; without this check a
        # "reload the 194 failed days" run silently re-processed all ~5,100 of them.
        if not force and self._already_done(con, business_date):
            return self._finish(con, RunResult(run_id, STATUS_SKIPPED, business_date, src,
                                               message="already ingested"), started, None)

        # 1. obtain bytes - from the lake when replaying, else from the network
        obj: LakeObject | None = None
        try:
            if from_lake:
                obj = self._lake_object_for(business_date)
                if obj is None:
                    return self._finish(con, RunResult(run_id, STATUS_SKIPPED, business_date,
                                                       src, message="not in lake"), started, None)
                payload = self.lake.read(obj)
                # Prefetch lands bytes without touching the database, so the manifest
                # row is written here, at first use. Idempotent.
                self._record_manifest(con, obj)
            else:
                res = self.provider.fetch(business_date)
                obj = self.lake.put(
                    source=src, dataset=self.provider.dataset, business_date=business_date,
                    filename=res.filename, payload=res.payload, url=res.url,
                    content_type=res.content_type, http_status=res.http_status,
                    retrieved_at=res.retrieved_at,
                )
                payload = res.payload
                self._record_manifest(con, obj)
        except NotPublished as e:
            return self._finish(con, RunResult(run_id, STATUS_NOT_PUBLISHED, business_date,
                                               src, message=str(e)), started, None)
        except FetchError as e:
            return self._finish(con, RunResult(run_id, STATUS_FAILED, business_date, src,
                                               message=str(e)), started, None)

        # 2. parse, gate on quality, publish
        csv_bytes = type(self.provider).to_csv(payload)
        staging = self.cfg.quarantine / f".staging_{run_id}.csv"
        staging.write_bytes(csv_bytes)

        try:
            con.execute(
                f"CREATE OR REPLACE TEMP TABLE raw AS "
                f"SELECT * FROM read_csv_auto('{staging.as_posix()}', header=true, all_varchar=true)"
            )
            rows_in = con.execute("SELECT COUNT(*) FROM raw").fetchone()[0]

            report = quality.check_bhavcopy(con, "raw", business_date=business_date)
            self._record_dq(con, run_id, report)

            # A file-scope failure means the payload as a whole is untrustworthy.
            if not report.publishable:
                self._quarantine(business_date, csv_bytes, run_id)
                return self._finish(
                    con, RunResult(run_id, STATUS_QUARANTINED, business_date, src,
                                   rows_in=rows_in, message=report.summary()), started, obj)

            # Row-scope failures reject rows, not the file. Exchanges do publish
            # impossible rows; discarding a whole day over one of them is the wrong trade.
            n_rejected = quality.partition_rows(con, "raw", report)
            if n_rejected:
                self._record_rejects(con, run_id, business_date, src)

            rows_out = self._write_curated(con, business_date, obj)
            sec_master.upsert_from_bhavcopy(con, "raw_clean", business_date, src)
            universe.write_snapshot(con, "raw_clean", business_date, src)

            status = STATUS_OK if not n_rejected else STATUS_PARTIAL
            return self._finish(
                con, RunResult(run_id, status, business_date, src, rows_in=rows_in,
                               rows_out=rows_out, rows_rejected=n_rejected,
                               message=report.summary()), started, obj)
        except Exception as e:  # noqa: BLE001 - a parse failure must not kill a batch
            self._quarantine(business_date, csv_bytes, run_id)
            return self._finish(con, RunResult(run_id, STATUS_FAILED, business_date, src,
                                               message=f"{type(e).__name__}: {e}"), started, obj)
        finally:
            staging.unlink(missing_ok=True)

    # -------------------------------------------------------------- internals
    def _already_done(self, con, business_date: date) -> bool:
        row = con.execute(
            """SELECT COUNT(*) FROM ingest_runs
               WHERE job = ? AND source = ? AND business_date = ?
                 AND status IN (?, ?)""",
            [self.job, self.provider.source, business_date, STATUS_OK, STATUS_PARTIAL],
        ).fetchone()
        return bool(row and row[0])

    def _lake_object_for(self, business_date: date) -> LakeObject | None:
        """Look up the day's payload by path.

        Scanning every lake object per date is O(n) and becomes the bottleneck once the
        lake holds a decade, so the day folder is addressed directly.
        """
        day_dir = (self.cfg.lake / self.provider.source / self.provider.dataset /
                   f"{business_date:%Y}" / f"{business_date:%m}" / f"{business_date:%d}")
        if not day_dir.exists():
            return None
        for meta in sorted(day_dir.glob("*.meta.json")):
            rel = meta.relative_to(self.cfg.lake).as_posix()[: -len(".meta.json")]
            obj = self.lake.meta(rel)
            if obj:
                return obj
        return None

    def _record_manifest(self, con, obj: LakeObject) -> None:
        con.execute(
            """INSERT INTO lake_manifest
               (key, source, dataset, business_date, filename, url, retrieved_at,
                sha256, size_bytes, http_status, content_type)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT (key) DO NOTHING""",
            [obj.key, obj.source, obj.dataset, obj.business_date, obj.filename, obj.url,
             obj.retrieved_at, obj.sha256, obj.size_bytes, obj.http_status, obj.content_type],
        )

    def _record_dq(self, con, run_id: str, report: quality.QualityReport) -> None:
        for r in report.results:
            con.execute(
                """INSERT INTO dq_results
                   (run_id, check_name, severity, passed, scope, observed, detail, checked_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                [run_id, r.name, r.severity, r.passed, r.scope, r.observed, r.detail,
                 r.checked_at],
            )

    def _record_rejects(self, con, run_id: str, business_date: date, source: str) -> None:
        con.execute(
            """INSERT INTO rejected_rows
               (run_id, source, business_date, isin, ticker, reject_reason, raw_row, rejected_at)
               SELECT ?, ?, ?, TRIM(ISIN), TRIM(TckrSymb), reject_reason,
                      CONCAT_WS('|', TckrSymb, ISIN, OpnPric, HghPric, LwPric, ClsPric,
                                TtlTradgVol),
                      ?
               FROM raw_rejected""",
            [run_id, source, business_date, datetime.now(timezone.utc)],
        )

    def _quarantine(self, business_date: date, payload: bytes, run_id: str) -> None:
        p = self.cfg.quarantine / self.provider.source / f"{business_date}_{run_id}.csv"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(payload)

    def _write_curated(self, con, business_date: date, obj: LakeObject | None) -> int:
        """Normalise UDiFF into the curated schema and write one Parquet partition."""
        out_dir = (self.cfg.curated / "eod_prices" /
                   f"exchange={self.provider.source}" / f"business_date={business_date}")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "part-0.parquet"
        evidence = obj.key if obj else ""

        con.execute(
            f"""
            COPY (
                SELECT
                    TRY_CAST(TradDt AS DATE)            AS trade_date,
                    TRIM(ISIN)                          AS isin,
                    TRIM(TckrSymb)                      AS ticker,
                    NULLIF(TRIM(SctySrs), '')           AS series,
                    TRIM(FinInstrmTp)                   AS instrument_type,
                    TRIM(FinInstrmId)                   AS instrument_id,
                    TRIM(FinInstrmNm)                   AS name,
                    TRY_CAST(OpnPric AS DOUBLE)         AS open_price,
                    TRY_CAST(HghPric AS DOUBLE)         AS high_price,
                    TRY_CAST(LwPric  AS DOUBLE)         AS low_price,
                    TRY_CAST(ClsPric AS DOUBLE)         AS close_price,
                    TRY_CAST(LastPric AS DOUBLE)        AS last_price,
                    TRY_CAST(PrvsClsgPric AS DOUBLE)    AS prev_close,
                    TRY_CAST(SttlmPric AS DOUBLE)       AS settlement_price,
                    TRY_CAST(TtlTradgVol AS BIGINT)     AS traded_volume,
                    TRY_CAST(TtlTrfVal AS DOUBLE)       AS turnover,
                    TRY_CAST(TtlNbOfTxsExctd AS BIGINT) AS trades,
                    '{evidence}'                        AS evidence_key
                FROM raw_clean
                WHERE TRIM(ISIN) <> ''
            ) TO '{out_file.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
        return con.execute("SELECT COUNT(*) FROM raw_clean WHERE TRIM(ISIN) <> ''").fetchone()[0]

    def _finish(self, con, result: RunResult, started: datetime,
                obj: LakeObject | None) -> RunResult:
        con.execute(
            """INSERT INTO ingest_runs
               (run_id, job, source, dataset, business_date, started_at, finished_at,
                status, rows_in, rows_out, rows_rejected, lake_key, message)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [result.run_id, self.job, self.provider.source, self.provider.dataset,
             result.business_date, started, datetime.now(timezone.utc), result.status,
             result.rows_in, result.rows_out, result.rows_rejected,
             obj.key if obj else None, result.message[:2000]],
        )
        return result

    # ---------------------------------------------------------------- batches
    def run_range(self, start: date, end: date, *, force: bool = False,
                  from_lake: bool = False) -> list[RunResult]:
        """Ingest a whole range on one connection - far faster than per-date connects."""
        out: list[RunResult] = []
        with self.db.connect() as con:
            for d in business_days(start, end):
                out.append(self._run(con, d, force=force, from_lake=from_lake))
        return out


def business_days(start: date, end: date):
    """Weekdays between two dates. Exchange holidays surface as ``not_published``."""
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)
