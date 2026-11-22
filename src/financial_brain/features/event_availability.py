"""Availability-timed event indicators for filings, results and shareholding updates.

Exchange publication time, not the period end or the day a crawler ran, determines the
first session on which an event may enter a feature. The detail table retains event IDs,
publication and collector clocks. A separate session panel exposes rolling counts for
corporate announcements, results, shareholding and statement revisions.
"""
from __future__ import annotations

from typing import Any

VERSION = "ea1"
OPEN_HOUR = 9
ROLLING_WINDOWS = {
    "CORPORATE_ANNOUNCEMENT": 5,
    "FINANCIAL_RESULT": 5,
    "SHAREHOLDING_UPDATE": 60,
    "STATEMENT_REVISION": 60,
}


def _table(con: Any, name: str) -> bool:
    return bool(con.execute("SELECT 1 FROM information_schema.tables WHERE table_name = ?", [name]).fetchone())


def _source_queries(con: Any) -> list[str]:
    queries = []
    if _table(con, "announcements"):
        queries.append("""SELECT a.isin, a.news_id AS event_id,
            CASE WHEN a.event_type = 'RESULTS' THEN 'FINANCIAL_RESULT'
                 ELSE 'CORPORATE_ANNOUNCEMENT' END AS event_type,
            a.published_at AS published_at, a.observed_at AS source_recorded_at,
            a.source AS source
            FROM announcements a WHERE a.isin IS NOT NULL AND a.published_at IS NOT NULL""")
    if _table(con, "xbrl_filings"):
        queries.append("""SELECT x.isin, x.seq_number AS event_id, 'FINANCIAL_RESULT' AS event_type,
            x.filed_at AS published_at, x.attempted_at AS source_recorded_at,
            'NSE_XBRL' AS source
            FROM xbrl_filings x WHERE x.isin IS NOT NULL AND x.filed_at IS NOT NULL""")
    if _table(con, "shareholding_filings"):
        queries.append("""SELECT s.isin, s.record_id AS event_id, 'SHAREHOLDING_UPDATE' AS event_type,
            s.known_at AS published_at, s.observed_at AS source_recorded_at,
            'NSE_SHAREHOLDING' AS source
            FROM shareholding_filings s WHERE s.isin IS NOT NULL AND s.known_at IS NOT NULL""")
    if _table(con, "fundamental_observation"):
        queries.append("""SELECT f.isin, MIN(f.seq_number) AS event_id, 'STATEMENT_REVISION' AS event_type,
            f.filed_at AS published_at, NULL::TIMESTAMPTZ AS source_recorded_at,
            'NSE_XBRL_REVISION' AS source
            FROM fundamental_observation f
            WHERE f.isin IS NOT NULL AND f.filed_at IS NOT NULL
            GROUP BY f.isin, f.filed_at, f.period_end, f.basis
            HAVING MAX(f.revision) > 1""")
    return queries


def build(con) -> dict:
    """Rebuild event detail and session-level event availability features."""
    if not _table(con, "adjusted_prices") or not _table(con, "security_lineage"):
        return {"version": VERSION, "status": "UNAVAILABLE", "reason": "PRICE_CALENDAR_OR_LINEAGE_MISSING",
                "events": 0, "feature_rows": 0, "sources": []}
    queries = _source_queries(con)
    if queries:
        source_sql = "\nUNION ALL\n".join(queries)
        con.execute(f"""CREATE OR REPLACE TABLE event_availability AS
            WITH calendar AS (
                SELECT DISTINCT business_date FROM adjusted_prices
            ), raw_events AS ({source_sql}), identified AS (
                SELECT l.lineage, e.event_id, e.event_type, e.published_at,
                       e.source_recorded_at, e.source,
                       CAST(e.published_at AS DATE)
                         + CASE WHEN EXTRACT(hour FROM e.published_at) >= {OPEN_HOUR}
                                THEN INTERVAL 1 DAY ELSE INTERVAL 0 DAY END AS eligible_date
                FROM raw_events e JOIN security_lineage l ON l.isin = e.isin
                WHERE e.published_at IS NOT NULL
            )
            SELECT i.lineage, i.event_id, i.event_type, i.published_at,
                   i.source_recorded_at, i.source,
                   (SELECT MIN(c.business_date) FROM calendar c
                    WHERE c.business_date >= i.eligible_date) AS available_session
            FROM identified i
            WHERE EXISTS (SELECT 1 FROM calendar c WHERE c.business_date >= i.eligible_date)
        """)
    else:
        con.execute("""CREATE OR REPLACE TABLE event_availability (
            lineage VARCHAR, event_id VARCHAR, event_type VARCHAR, published_at TIMESTAMP,
            source_recorded_at TIMESTAMPTZ, source VARCHAR, available_session DATE)""")

    con.execute("""CREATE OR REPLACE TABLE event_availability_features AS
        WITH cal AS (
            SELECT business_date, ROW_NUMBER() OVER (ORDER BY business_date) AS session_n
            FROM (SELECT DISTINCT business_date FROM adjusted_prices)
        ), counts AS (
            SELECT lineage, available_session AS business_date,
                   COUNT(*) FILTER (WHERE event_type = 'CORPORATE_ANNOUNCEMENT') AS announcement_n,
                   COUNT(*) FILTER (WHERE event_type = 'FINANCIAL_RESULT') AS result_n,
                   COUNT(*) FILTER (WHERE event_type = 'SHAREHOLDING_UPDATE') AS shareholding_n,
                   COUNT(*) FILTER (WHERE event_type = 'STATEMENT_REVISION') AS revision_n
            FROM event_availability WHERE available_session IS NOT NULL
            GROUP BY lineage, available_session
        ), panel AS (
            SELECT f.lineage, f.business_date, c.session_n,
                   COALESCE(e.announcement_n, 0) AS announcement_n,
                   COALESCE(e.result_n, 0) AS result_n,
                   COALESCE(e.shareholding_n, 0) AS shareholding_n,
                   COALESCE(e.revision_n, 0) AS revision_n
            FROM features f JOIN cal c USING (business_date)
            LEFT JOIN counts e ON e.lineage = f.lineage AND e.business_date = f.business_date
        ), rolled AS (
            SELECT *,
                   SUM(announcement_n) OVER (PARTITION BY lineage ORDER BY business_date
                       ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS announcements_5s,
                   SUM(result_n) OVER (PARTITION BY lineage ORDER BY business_date
                       ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS results_5s,
                   SUM(shareholding_n) OVER (PARTITION BY lineage ORDER BY business_date
                       ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS shareholding_updates_60s,
                   SUM(revision_n) OVER (PARTITION BY lineage ORDER BY business_date
                       ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS statement_revisions_60s,
                   MAX(CASE WHEN result_n > 0 THEN session_n END)
                       OVER (PARTITION BY lineage ORDER BY business_date
                             ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS last_result_n
            FROM panel
        )
        SELECT lineage, business_date, announcements_5s, results_5s,
               shareholding_updates_60s, statement_revisions_60s,
               CASE WHEN last_result_n IS NOT NULL THEN session_n - last_result_n END AS sessions_since_result,
               'ea1' AS version
        FROM rolled""")
    rows = con.execute("SELECT COUNT(*) FROM event_availability").fetchone()[0]
    feature_rows = con.execute("SELECT COUNT(*) FROM event_availability_features").fetchone()[0]
    type_rows = con.execute("SELECT event_type, COUNT(*) FROM event_availability GROUP BY event_type").fetchall()
    return {"version": VERSION, "status": "AVAILABLE", "events": rows, "feature_rows": feature_rows,
            "by_type": dict(type_rows), "sources": [q.split("FROM ")[1].split()[0] for q in queries]}


__all__ = ["ROLLING_WINDOWS", "VERSION", "build"]
