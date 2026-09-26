"""Per-event-type flags, on the session each filing could first be traded.

The event archive is the strongest data this project holds - 3.08M classified
announcements - and until now it has been used only in aggregate: ``news_20d`` counts
*any* high-materiality filing, ``adverse_60d`` counts a fixed bundle of bad ones. Both
were rejected, and both had the same defect the insider feature turned out to have:
mixing event types that mean opposite things into one number.

The analogue work measured what actually follows each type, and the spread is large:

    AUDITOR_RESIGNATION   -3.58%        JOINT_VENTURE   +1.67%
    INSOLVENCY            -3.29%        ORDER_WIN       +1.49%
    CLARIFICATION         -2.54%        FUND_RAISING    +0.62%

A single count over all of them averages +1.67% against -3.58% and gets approximately
nothing, which is what was measured. These flags keep the types apart so each can be
tested as the literature tests it: an event study, not a cross-sectional rank.

## Point in time, and the one hour that matters

A filing published at 18:00 cannot be traded that day. The session a filing is first
tradeable in is computed exactly as ``features/events.py`` computes it - published date,
plus one if the hour is at or past the open, mapped forward to the next session that
exists. Getting this wrong by one day would manufacture an edge out of the announcement
itself, which is the most obvious look-ahead available in an event study and the reason
the convention is shared rather than reimplemented here.

## Materiality

Only ``high`` materiality rows set a flag, matching the population the analogue base rates
were measured over. A distribution measured on material filings does not describe routine
ones - the lesson from the insider feature, where 30.6% of the signal turned out to be
quarterly compliance certificates.
"""
from __future__ import annotations

VERSION = "ef1"
OPEN_HOUR = 9

#: The types worth separating, each with the measured base rate that justifies testing it.
#: Chosen from the analogue catalogue by evidence, not by taste: every one either has a
#: material measured excess or disagrees with its own literature in sign.
TRACKED = {
    "AUDITOR_RESIGNATION": "-3.58% over 196 cases; Wells & Loudder (1997)",
    "INSOLVENCY": "-3.29% over 144 cases; Campbell, Hilscher & Szilagyi (2008)",
    "CLARIFICATION": "-2.54% over 6,795 cases; Hribar & Jenkins (2004)",
    "SCHEME": "-1.85%; Cusatis et al. (1993) predict the OPPOSITE sign",
    "JOINT_VENTURE": "+1.67% over 277 cases; McConnell & Nantell (1985)",
    "ORDER_WIN": "+1.49% over 5,557 cases, but a -2.29% median",
    "FUND_RAISING": "+0.62%; Loughran & Ritter (1995) predict the OPPOSITE sign",
    "ACQUISITION": "+0.41% over 6,370 cases; Asquith (1983)",
    "PROMOTER_PLEDGE": "+0.23%; India-specific, 27,688 filings, no US equivalent",
    "CREDIT_RATING": "-0.03%; the own-record scorecard flagged these prompts as losers",
    "MANAGEMENT_CHANGE": "-0.21%; Denis & Denis (1995)",
    "BUYBACK": "5,803 filings; Ikenberry, Lakonishok & Vermaelen (1995)",
}


def _column(event_type: str) -> str:
    return "e_" + event_type.lower()


def build_sql() -> str:
    flags = ",\n           ".join(
        f"MAX(CASE WHEN a.event_type = '{t}' THEN 1 ELSE 0 END) AS {_column(t)}"
        for t in TRACKED)
    return f"""
CREATE OR REPLACE TEMP TABLE _efcal AS
    SELECT business_date, ROW_NUMBER() OVER (ORDER BY business_date) AS k
    FROM (SELECT DISTINCT business_date FROM adjusted_prices);

-- The session a filing published on a given calendar day could first be traded in.
-- Identical convention to features/events.py, deliberately shared rather than rewritten.
CREATE OR REPLACE TEMP TABLE _efnext AS
    SELECT day, MIN(k) OVER (ORDER BY day DESC ROWS UNBOUNDED PRECEDING) AS k
    FROM (
        SELECT d.day, c.k
        FROM (SELECT UNNEST(generate_series(
                  (SELECT MIN(business_date) FROM _efcal) - INTERVAL 5 DAY,
                  (SELECT MAX(business_date) FROM _efcal) + INTERVAL 5 DAY,
                  INTERVAL 1 DAY))::DATE AS day) d
        LEFT JOIN _efcal c ON c.business_date = d.day
    );

CREATE OR REPLACE TABLE event_flags AS
SELECT c.business_date, l.lineage, {flags.replace('a.event_type', 'a.event_type')}
FROM announcements a
JOIN security_lineage l ON l.isin = a.isin
JOIN _efnext n ON n.day = CAST(a.published_at AS DATE)
     + CASE WHEN EXTRACT(hour FROM a.published_at) >= {OPEN_HOUR} THEN 1 ELSE 0 END
JOIN _efcal c ON c.k = n.k
WHERE a.isin IS NOT NULL AND a.published_at IS NOT NULL
  AND a.materiality = 'high'
GROUP BY 1, 2;
"""


def build(con) -> dict:
    for statement in build_sql().strip().split(";\n"):
        if statement.strip():
            con.execute(statement)
    cols = ", ".join(f"SUM({_column(t)})" for t in TRACKED)
    row = con.execute(f"SELECT COUNT(*), {cols} FROM event_flags").fetchone()
    return {"version": VERSION, "rows": row[0],
            "fired": {t: int(v or 0) for t, v in zip(TRACKED, row[1:], strict=True)}}


def columns() -> list[str]:
    return [_column(t) for t in TRACKED]
