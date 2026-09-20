"""Reference-data and benchmark ingestion jobs.

Same contract as the bhavcopy job: land the raw payload in the immutable lake first,
record provenance, then parse. These are lower-volume and lower-risk than price data, so
they share the machinery rather than duplicating it.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from ..config import Config, load
from ..lake.store import RawLake
from ..providers.base import FetchError, NotPublished
from ..providers.reference import (NSEEquityListProvider, NSEFnoLotsProvider,
                                   NSEIndexCloseProvider)
from ..storage.db import Database
from .job import (STATUS_FAILED, STATUS_NOT_PUBLISHED, STATUS_OK, STATUS_SKIPPED,
                  RunResult, business_days)


class _RefJobBase:
    job = "reference_ingest"

    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or load()
        self.lake = RawLake(self.cfg.lake)
        self.db = Database(self.cfg)

    def _land(self, con, provider, business_date: date):
        res = provider.fetch(business_date)
        obj = self.lake.put(
            source=provider.source, dataset=provider.dataset, business_date=business_date,
            filename=res.filename, payload=res.payload, url=res.url,
            content_type=res.content_type, http_status=res.http_status,
            retrieved_at=res.retrieved_at)
        con.execute(
            """INSERT INTO lake_manifest
               (key, source, dataset, business_date, filename, url, retrieved_at,
                sha256, size_bytes, http_status, content_type)
               VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (key) DO NOTHING""",
            [obj.key, obj.source, obj.dataset, obj.business_date, obj.filename, obj.url,
             obj.retrieved_at, obj.sha256, obj.size_bytes, obj.http_status, obj.content_type])
        return res.payload, obj

    def _from_lake(self, con, provider, business_date: date):
        """Return (payload, obj) if the lake already holds this day, else None."""
        day_dir = (self.cfg.lake / provider.source / provider.dataset /
                   f"{business_date:%Y}" / f"{business_date:%m}" / f"{business_date:%d}")
        if not day_dir.exists():
            return None
        for meta in sorted(day_dir.glob("*.meta.json")):
            obj = self.lake.meta(meta.relative_to(self.cfg.lake).as_posix()[: -len(".meta.json")])
            if obj:
                self._register(con, obj)
                return self.lake.read(obj), obj
        return None

    def _register(self, con, obj) -> None:
        con.execute(
            """INSERT INTO lake_manifest
               (key, source, dataset, business_date, filename, url, retrieved_at,
                sha256, size_bytes, http_status, content_type)
               VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (key) DO NOTHING""",
            [obj.key, obj.source, obj.dataset, obj.business_date, obj.filename, obj.url,
             obj.retrieved_at, obj.sha256, obj.size_bytes, obj.http_status, obj.content_type])

    def _finish(self, con, result: RunResult, started, obj, dataset: str) -> RunResult:
        con.execute(
            """INSERT INTO ingest_runs
               (run_id, job, source, dataset, business_date, started_at, finished_at,
                status, rows_in, rows_out, rows_rejected, lake_key, message)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [result.run_id, self.job, "NSE", dataset, result.business_date, started,
             datetime.now(timezone.utc), result.status, result.rows_in, result.rows_out,
             0, obj.key if obj else None, result.message[:2000]])
        return result


class IndexCloseJob(_RefJobBase):
    """Daily close for every NSE index - the benchmark history."""

    def run(self, business_date: date, *, force: bool = False) -> RunResult:
        with self.db.connect() as con:
            return self._run(con, business_date, force=force)

    def _run(self, con, business_date: date, *, force: bool) -> RunResult:
        run_id, started = uuid.uuid4().hex[:16], datetime.now(timezone.utc)
        if not force:
            done = con.execute(
                "SELECT COUNT(*) FROM index_levels WHERE business_date = ?",
                [business_date]).fetchone()[0]
            if done:
                return self._finish(con, RunResult(run_id, STATUS_SKIPPED, business_date,
                                                   "NSE", message="already ingested"),
                                    started, None, "index_close")
        try:
            # Lake first: `fb prefetch --kind index` lands these bytes without the
            # database, and bytes we already hold are never fetched again.
            held = self._from_lake(con, NSEIndexCloseProvider(), business_date)
            payload, obj = held if held else self._land(
                con, NSEIndexCloseProvider(), business_date)
        except NotPublished as e:
            return self._finish(con, RunResult(run_id, STATUS_NOT_PUBLISHED, business_date,
                                               "NSE", message=str(e)), started, None,
                                "index_close")
        except FetchError as e:
            return self._finish(con, RunResult(run_id, STATUS_FAILED, business_date, "NSE",
                                               message=str(e)), started, None, "index_close")

        rows = NSEIndexCloseProvider.parse(payload, business_date)
        now = datetime.now(timezone.utc)
        con.execute("DELETE FROM index_levels WHERE business_date = ? AND source = 'NSE'",
                    [business_date])
        for r in rows:
            con.execute(
                """INSERT INTO index_levels
                   (business_date, index_name, open_level, high_level, low_level,
                    close_level, variant, source, observed_at)
                   VALUES (?,?,?,?,?,?,'PRICE','NSE',?)
                   ON CONFLICT (business_date, index_name, variant) DO NOTHING""",
                [r["business_date"], r["index_name"], r["open_level"], r["high_level"],
                 r["low_level"], r["close_level"], now])
        return self._finish(con, RunResult(run_id, STATUS_OK, business_date, "NSE",
                                           rows_in=len(rows), rows_out=len(rows),
                                           message=f"{len(rows)} indices"),
                            started, obj, "index_close")

    def run_range(self, start: date, end: date, *, force: bool = False) -> list[RunResult]:
        with self.db.connect() as con:
            return [self._run(con, d, force=force) for d in business_days(start, end)]


class EquityListJob(_RefJobBase):
    """NSE master list: ISIN, series, listing date, face value, market lot."""

    def run(self, business_date: date | None = None) -> RunResult:
        business_date = business_date or date.today()
        run_id, started = uuid.uuid4().hex[:16], datetime.now(timezone.utc)
        with self.db.connect() as con:
            try:
                payload, obj = self._land(con, NSEEquityListProvider(), business_date)
            except FetchError as e:
                return self._finish(con, RunResult(run_id, STATUS_FAILED, business_date,
                                                   "NSE", message=str(e)), started, None,
                                    "equity_list")
            rows = NSEEquityListProvider.parse(payload)
            now = datetime.now(timezone.utc)
            for r in rows:
                con.execute(
                    """INSERT INTO security_reference
                       (isin, exchange, symbol, company_name, series, listing_date,
                        paid_up_value, face_value, market_lot, source, observed_at,
                        evidence_key)
                       VALUES (?,?,?,?,?,?,?,?,?,'NSE',?,?)
                       ON CONFLICT (isin, exchange, series) DO UPDATE SET
                           symbol = EXCLUDED.symbol,
                           company_name = EXCLUDED.company_name,
                           listing_date = EXCLUDED.listing_date,
                           face_value = EXCLUDED.face_value,
                           market_lot = EXCLUDED.market_lot,
                           observed_at = EXCLUDED.observed_at""",
                    [r["isin"], r["exchange"], r["symbol"], r["company_name"], r["series"],
                     r["listing_date"], r["paid_up_value"], r["face_value"],
                     r["market_lot"], now, obj.key])
            return self._finish(con, RunResult(run_id, STATUS_OK, business_date, "NSE",
                                               rows_in=len(rows), rows_out=len(rows),
                                               message=f"{len(rows)} securities"),
                                started, obj, "equity_list")


class FnoEligibilityJob(_RefJobBase):
    """F&O underlyings -> the FNO_ELIGIBLE flag.

    This is the binary "can this be shorted at all?" test that the India
    Implementability Gate needs: Indian cash equities cannot be shorted beyond intraday,
    so a long-short hypothesis is only implementable on names with a future.
    """

    def run(self, business_date: date | None = None) -> RunResult:
        business_date = business_date or date.today()
        run_id, started = uuid.uuid4().hex[:16], datetime.now(timezone.utc)
        with self.db.connect() as con:
            try:
                payload, obj = self._land(con, NSEFnoLotsProvider(), business_date)
            except FetchError as e:
                return self._finish(con, RunResult(run_id, STATUS_FAILED, business_date,
                                                   "NSE", message=str(e)), started, None,
                                    "fo_lots")
            rows = NSEFnoLotsProvider.parse(payload)
            now = datetime.now(timezone.utc)
            matched = 0
            for r in rows:
                isin = con.execute(
                    """SELECT isin FROM security_listings
                       WHERE exchange = 'NSE' AND ticker = ?
                       ORDER BY last_seen DESC LIMIT 1""", [r["symbol"]]).fetchone()
                if not isin:
                    continue
                con.execute(
                    """INSERT INTO security_flags
                       (isin, exchange, flag, valid_from, source, observed_at)
                       VALUES (?, 'NSE', 'FNO_ELIGIBLE', ?, 'NSE', ?)
                       ON CONFLICT (isin, flag, valid_from) DO NOTHING""",
                    [isin[0], business_date, now])
                matched += 1
            return self._finish(
                con, RunResult(run_id, STATUS_OK, business_date, "NSE", rows_in=len(rows),
                               rows_out=matched,
                               message=f"{matched}/{len(rows)} underlyings resolved to ISIN"),
                started, obj, "fo_lots")
