"""Event features: what a company disclosed, as a number a factor test can use (C12).

The control on the replayed record showed the *universe* - companies that filed something
material - returning +5% over 90 days while the committee picking from it returned
+3.15%. That is an event-driven effect nothing could test, because the firewall only
evaluates columns in ``features`` and every column there came from prices. These are the
missing columns.

Two rules keep them honest:

**A filing counts from the session you could have traded on it.** A disclosure published
at 16:00 is not actionable that day. Each filing is therefore assigned to the first
session *after* it became public (same session only if it was published before the 09:00
open), so a feature dated *t* never sees news the market had not yet had a chance to
price.

**Only counts, never readings.** These features count what was filed and how recently.
They deliberately do not include the model's tone verdict: the sentiment routes are
calibrated on filings and headlines, their coverage changes over time, and a factor test
running back to 2015 would be measuring the model's history rather than the market's.

Features (per lineage, per session):
    news_5d, news_20d      high-materiality filings in the last 5 / 20 sessions
    dealing_60d            disclosures of an actual dealing, 60 sessions. Named for
                           what it holds: until 2026-09-22 this was `insider_60d`
                           and 33.4% of it was compliance filings reporting no trade.
    adverse_60d            red-flag filings (insolvency, auditor, pledge, legal), 60
    days_since_news        sessions since the last high-materiality filing (NULL if none)
"""
from __future__ import annotations

VERSION = "e1"
OPEN_HOUR = 9                    # IST; a filing published after this trades next session

INSIDER = ("INSIDER_DISCLOSURE", "SUBSTANTIAL_ACQUISITION")
ADVERSE = ("INSOLVENCY", "AUDITOR_CHANGE", "AUDITOR_RESIGNATION", "PROMOTER_PLEDGE",
           "LEGAL_REGULATORY")

EVENTS_SQL = f"""
CREATE OR REPLACE TEMP TABLE _cal AS
    SELECT business_date, ROW_NUMBER() OVER (ORDER BY business_date) AS k
    FROM (SELECT DISTINCT business_date FROM adjusted_prices);

-- For every calendar day, the session a filing published that day could first be traded
-- in. Scanning days backwards, the running minimum of k over later days is exactly that.
CREATE OR REPLACE TEMP TABLE _next_session AS
    SELECT day, MIN(k) OVER (ORDER BY day DESC ROWS UNBOUNDED PRECEDING) AS k
    FROM (
        SELECT d.day, c.k
        FROM (SELECT UNNEST(generate_series(
                  (SELECT MIN(business_date) FROM _cal) - INTERVAL 5 DAY,
                  (SELECT MAX(business_date) FROM _cal) + INTERVAL 5 DAY,
                  INTERVAL 1 DAY))::DATE AS day) d
        LEFT JOIN _cal c ON c.business_date = d.day
    );

CREATE OR REPLACE TEMP TABLE _events AS
    SELECT l.lineage, n.k,
           COUNT(*) FILTER (WHERE a.materiality = 'high') AS high_n,
           COUNT(*) FILTER (WHERE a.event_type IN {INSIDER}) AS insider_n,
           COUNT(*) FILTER (WHERE a.event_type IN {ADVERSE}) AS adverse_n
    FROM announcements a
    JOIN security_lineage l ON l.isin = a.isin
    JOIN _next_session n ON n.day = CAST(a.published_at AS DATE)
         + CASE WHEN EXTRACT(hour FROM a.published_at) >= {OPEN_HOUR} THEN 1 ELSE 0 END
    WHERE a.isin IS NOT NULL AND a.published_at IS NOT NULL
    GROUP BY 1, 2;

CREATE OR REPLACE TABLE features AS
WITH panel AS (
    SELECT f.*, c.k,
           COALESCE(e.high_n, 0) AS high_n,
           COALESCE(e.insider_n, 0) AS insider_n,
           COALESCE(e.adverse_n, 0) AS adverse_n
    FROM features f
    JOIN _cal c USING (business_date)
    LEFT JOIN _events e ON e.lineage = f.lineage AND e.k = c.k
), rolled AS (
    SELECT *,
        SUM(high_n)    OVER (w ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)  AS news_5d,
        SUM(high_n)    OVER (w ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS news_20d,
        SUM(insider_n) OVER (w ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS dealing_60d,
        SUM(adverse_n) OVER (w ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS adverse_60d,
        MAX(CASE WHEN high_n > 0 THEN k END)
            OVER (w ROWS UNBOUNDED PRECEDING) AS last_news_k
    FROM panel
    WINDOW w AS (PARTITION BY lineage ORDER BY business_date)
)
SELECT * EXCLUDE (k, high_n, insider_n, adverse_n, last_news_k),
       CASE WHEN last_news_k IS NOT NULL THEN k - last_news_k END AS days_since_news
FROM rolled;
"""

COLUMNS = ("news_5d", "news_20d", "dealing_60d", "adverse_60d", "days_since_news")


def build(con) -> dict:
    """Add the event columns to ``features``. Run after features/indicators.build."""
    for statement in EVENTS_SQL.strip().split(";\n"):
        if statement.strip():
            con.execute(statement)
    row = con.execute("""SELECT COUNT(*), SUM(news_20d), AVG(dealing_60d)
                         FROM features""").fetchone()
    return {"version": VERSION, "rows": row[0], "news_20d_total": int(row[1] or 0),
            "dealing_60d_mean": round(float(row[2] or 0), 3)}
