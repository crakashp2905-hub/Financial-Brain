"""Ingest BSE announcements: lake first, classify, resolve ISIN as of the day.

Same contract as every other job - bytes land in the immutable lake before anything is
parsed, every run is recorded, replays need no network. One quality contract specific to
this feed: BSE declares how many announcements a day holds, and the parsed count must
reach it. A day that silently lost pages would otherwise look complete.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone

from ..config import Config, load
from ..events.classify import classify
from ..lake.store import RawLake
from ..providers.base import FetchError, NotPublished
from ..providers.bse_announcements import BSEAnnouncementsProvider
from ..storage.db import Database
from .job import (STATUS_FAILED, STATUS_NOT_PUBLISHED, STATUS_OK, STATUS_PARTIAL,
                  STATUS_SKIPPED, RunResult, session_candidates)

JOB, DATASET = "announcements_ingest", "announcements"
#: Parsed rows must reach this share of BSE's declared count for the day to be "ok".
MIN_COMPLETENESS = 0.99


class AnnouncementsJob:
    def __init__(self, cfg: Config | None = None, provider=None):
        self.cfg = cfg or load()
        self.provider = provider or BSEAnnouncementsProvider()
        self.lake = RawLake(self.cfg.lake)
        self.db = Database(self.cfg)

    def run_range(self, start: date, end: date, *, force: bool = False) -> list[RunResult]:
        with self.db.connect() as con:
            return [self._run(con, d, force=force) for d in session_candidates(start, end)]

    # ------------------------------------------------------------------ internals
    def _done(self, con, d: date) -> bool:
        return bool(con.execute(
            """SELECT COUNT(*) FROM ingest_runs WHERE job = ? AND business_date = ?
               AND status IN (?, ?, ?)""",
            [JOB, d, STATUS_OK, STATUS_PARTIAL, STATUS_NOT_PUBLISHED]).fetchone()[0])

    def _held(self, d: date):
        day = (self.cfg.lake / self.provider.source / self.provider.dataset /
               f"{d:%Y}" / f"{d:%m}" / f"{d:%d}")
        for meta in sorted(day.glob("*.meta.json")) if day.exists() else []:
            obj = self.lake.meta(meta.relative_to(self.cfg.lake).as_posix()[: -len(".meta.json")])
            if obj:
                return self.lake.read(obj), obj
        return None

    def _finish(self, con, res: RunResult, started, key) -> RunResult:
        con.execute(
            """INSERT INTO ingest_runs (run_id, job, source, dataset, business_date,
               started_at, finished_at, status, rows_in, rows_out, rows_rejected, lake_key,
               message) VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?)""",
            [res.run_id, JOB, "BSE", DATASET, res.business_date, started,
             datetime.now(timezone.utc), res.status, res.rows_in, res.rows_out, key,
             res.message[:2000]])
        return res

    def _run(self, con, d: date, *, force: bool) -> RunResult:
        run_id, started = uuid.uuid4().hex[:16], datetime.now(timezone.utc)
        if not force and self._done(con, d):
            return self._finish(con, RunResult(run_id, STATUS_SKIPPED, d, "BSE",
                                               message="already ingested"), started, None)
        try:
            held = self._held(d)
            if held:
                payload, obj = held
            else:
                res = self.provider.fetch(d)
                obj = self.lake.put(source=self.provider.source, dataset=self.provider.dataset,
                                    business_date=d, filename=res.filename,
                                    payload=res.payload, url=res.url,
                                    content_type=res.content_type,
                                    http_status=res.http_status,
                                    retrieved_at=res.retrieved_at)
                payload = res.payload
        except NotPublished as e:
            return self._finish(con, RunResult(run_id, STATUS_NOT_PUBLISHED, d, "BSE",
                                               message=str(e)), started, None)
        except FetchError as e:
            return self._finish(con, RunResult(run_id, STATUS_FAILED, d, "BSE",
                                               message=str(e)), started, None)

        con.execute(
            """INSERT INTO lake_manifest (key, source, dataset, business_date, filename, url,
               retrieved_at, sha256, size_bytes, http_status, content_type)
               VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (key) DO NOTHING""",
            [obj.key, obj.source, obj.dataset, obj.business_date, obj.filename, obj.url,
             obj.retrieved_at, obj.sha256, obj.size_bytes, obj.http_status, obj.content_type])

        declared, rows = self.provider.parse(payload)
        now = datetime.now(timezone.utc)
        con.execute("""CREATE OR REPLACE TEMP TABLE _ann (news_id VARCHAR, scrip VARCHAR,
            company VARCHAR, category VARCHAR, subcategory VARCHAR, headline VARCHAR,
            subject VARCHAR, event_type VARCHAR, materiality VARCHAR, rule VARCHAR,
            critical BOOLEAN, submitted_at TIMESTAMP, published_at TIMESTAMP,
            news_at TIMESTAMP, attachment VARCHAR)""")
        # Bulk-load through a newline-delimited JSON staging file: executemany binds
        # parameters row by row (~1.2 ms/row - 2.5 s for a 2,071-row day), which would
        # have made the 11-year backfill a six-hour job.
        staging = self.cfg.quarantine / f".staging_ann_{run_id}.jsonl"
        iso = lambda t: t.isoformat() if t else None
        with open(staging, "w", encoding="utf-8") as fh:
            for r in rows:
                kind, mat, rule = classify(r["category"], r["subcategory"], r["headline"],
                                           r["subject"])
                fh.write(json.dumps({
                    "news_id": r["news_id"], "scrip": r["scrip_code"], "company": r["company"],
                    "category": r["category"], "subcategory": r["subcategory"],
                    "headline": r["headline"], "subject": r["subject"], "event_type": kind,
                    "materiality": mat, "rule": rule, "critical": r["critical"],
                    "submitted_at": iso(r["submitted_at"]),
                    "published_at": iso(r["published_at"]), "news_at": iso(r["news_at"]),
                    "attachment": r["attachment"]}) + "\n")
        try:
            if rows:
                con.execute(f"""INSERT INTO _ann SELECT * FROM read_json('{staging.as_posix()}',
                    format = 'newline_delimited', columns = {{
                      'news_id': 'VARCHAR', 'scrip': 'VARCHAR', 'company': 'VARCHAR',
                      'category': 'VARCHAR', 'subcategory': 'VARCHAR', 'headline': 'VARCHAR',
                      'subject': 'VARCHAR', 'event_type': 'VARCHAR', 'materiality': 'VARCHAR',
                      'rule': 'VARCHAR', 'critical': 'BOOLEAN', 'submitted_at': 'TIMESTAMP',
                      'published_at': 'TIMESTAMP', 'news_at': 'TIMESTAMP',
                      'attachment': 'VARCHAR'}})""")
        finally:
            staging.unlink(missing_ok=True)

        written = con.execute(f"""
            WITH spans AS (
                SELECT instrument_id AS scrip, isin, MIN(first_seen) f, MAX(last_seen) l
                FROM security_listings WHERE exchange = 'BSE' AND instrument_id <> ''
                GROUP BY 1, 2
            ), pick AS (
                SELECT a.news_id, s.isin,
                       ROW_NUMBER() OVER (PARTITION BY a.news_id ORDER BY
                           CASE WHEN DATE '{d}' BETWEEN s.f AND s.l THEN 0
                                ELSE LEAST(ABS(date_diff('day', s.f, DATE '{d}')),
                                           ABS(date_diff('day', s.l, DATE '{d}'))) END) AS rk
                FROM _ann a JOIN spans s ON s.scrip = a.scrip
                WHERE DATE '{d}' BETWEEN s.f - 5 AND s.l + 5
            )
            INSERT INTO announcements
            SELECT a.news_id, 'BSE', DATE '{d}', a.scrip, p.isin, a.company, a.category,
                   a.subcategory, a.headline, a.subject, a.event_type, a.materiality, a.rule,
                   a.critical, a.submitted_at, a.published_at, a.news_at, a.attachment,
                   ?, ?
            FROM _ann a LEFT JOIN pick p ON p.news_id = a.news_id AND p.rk = 1
            -- The announcement is the source; its classification is derived. A --force
            -- replay therefore re-applies the current classifier (and ISIN resolution),
            -- but never alters what BSE published or when we first saw it. The one
            -- exception repairs our own parsing: a headline stored cut off ("....")
            -- takes the full text BSE sent in the same object.
            ON CONFLICT (news_id) DO UPDATE SET
                headline = CASE WHEN announcements.headline LIKE '%..'
                                THEN EXCLUDED.headline ELSE announcements.headline END,
                event_type = EXCLUDED.event_type, materiality = EXCLUDED.materiality,
                rule = EXCLUDED.rule, isin = COALESCE(EXCLUDED.isin, announcements.isin)
            RETURNING 1""", [obj.key, now]).fetchall()

        complete = len(rows) >= MIN_COMPLETENESS * declared if declared else True
        status = STATUS_OK if complete else STATUS_PARTIAL
        msg = (f"{len(rows)}/{declared} parsed, {len(written)} written"
               + ("" if complete else " - INCOMPLETE: fewer rows than BSE declared"))
        return self._finish(con, RunResult(run_id, status, d, "BSE", rows_in=declared,
                                           rows_out=len(written), message=msg), started,
                            obj.key)
