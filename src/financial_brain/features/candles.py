"""Candlestick patterns, defined precisely enough to be wrong.

Candlesticks are the most widely taught and least well evidenced part of technical
analysis. The serious tests are mostly negative - Marshall, Young & Rose (2006) find no
value in candlestick timing on Dow stocks; Lo, Mamaysky & Wang (2000) find some chart
patterns carry information but treat the result cautiously. So the prior here is weak,
and it is written down before anything is measured.

The reason to implement them anyway is that "the brain does not understand candles" was
true and unmeasured, and an untested belief is worse than a rejected one.

## Definitions are the whole problem

"A hammer" is not a definition. Every pattern below is stated as arithmetic over the
body, the two shadows and the preceding bars, with explicit thresholds:

    body      = |close - open|
    range     = high - low
    upper     = high - max(open, close)
    lower     = min(open, close) - low

Thresholds (``DOJI_BODY``, ``LONG_BODY``, ``SHADOW_RATIO``) are module constants, so a
pattern is a fixed rule rather than something adjusted until it works. Changing one is a
code change with a reason - and a new trial.

## Context matters and is included

A hammer is only a hammer after a decline; the identical shape after a rally is a hanging
man and is read the opposite way. Patterns that require a prior trend carry it in the
definition via a 5-session return, rather than being detected shape-only and quietly
inheriting whatever trend happened to be there.
"""
from __future__ import annotations

VERSION = "k1"

DOJI_BODY = 0.10        # body <= 10% of the range is indecision
LONG_BODY = 0.60        # body >= 60% of the range is conviction
SHADOW_RATIO = 2.0      # a defining shadow is >= 2x the body
SMALL_SHADOW = 0.10     # the opposite shadow is <= 10% of the range
TREND_RET = 0.03        # 3% over 5 sessions counts as a prior move

#: Each entry: the SQL predicate, what it is said to mean, and the honest prior.
PATTERNS: dict[str, dict] = {
    "doji": {
        "sql": f"body <= {DOJI_BODY} * rng",
        "means": "Indecision: buyers and sellers finished level.",
        "prior": "Weak. A doji is a statement about one session's disagreement, not a "
                 "direction, so it has no sign to predict with. Included as a control: "
                 "if a directionless pattern shows a signal, the harness is wrong.",
    },
    "hammer": {
        "sql": (f"body <= 0.35 * rng AND lower >= {SHADOW_RATIO} * body "
                f"AND upper <= {SMALL_SHADOW} * rng AND ret_5 <= -{TREND_RET}"),
        "means": "After a decline, sellers pushed it down and lost the session.",
        "prior": "Weak-positive. The best known reversal shape, and the one most likely "
                 "to be arbitraged away precisely because it is best known.",
    },
    "shooting_star": {
        "sql": (f"body <= 0.35 * rng AND upper >= {SHADOW_RATIO} * body "
                f"AND lower <= {SMALL_SHADOW} * rng AND ret_5 >= {TREND_RET}"),
        "means": "After a rally, buyers pushed it up and gave it all back.",
        "prior": "Weak-negative, the hammer's mirror. Long-only here, so it is tested as "
                 "a signal to *avoid*, not to short - shorting cash equity is not "
                 "available in India.",
    },
    "bullish_engulfing": {
        "sql": ("c > o AND prev_c < prev_o AND c >= prev_o AND o <= prev_c "
                f"AND body >= {LONG_BODY} * rng AND ret_5 <= -{TREND_RET}"),
        "means": "A down session wholly swallowed by the next up session.",
        "prior": "Weak-positive. Two-bar patterns at least use more information than one.",
    },
    "bearish_engulfing": {
        "sql": ("c < o AND prev_c > prev_o AND c <= prev_o AND o >= prev_c "
                f"AND body >= {LONG_BODY} * rng AND ret_5 >= {TREND_RET}"),
        "means": "An up session wholly swallowed by the next down session.",
        "prior": "Weak-negative.",
    },
    "morning_star": {
        "sql": ("prev2_c < prev2_o AND ABS(prev_c - prev_o) <= 0.3 * (prev_h - prev_l) "
                "AND c > o AND c > (prev2_o + prev2_c) / 2 "
                f"AND ret_5 <= -{TREND_RET}"),
        "means": "Down session, then hesitation, then a decisive up session.",
        "prior": "Weak-positive. Three bars, so rarer - capacity may bind before the "
                 "signal does.",
    },
    "marubozu_bull": {
        "sql": (f"c > o AND body >= 0.90 * rng AND upper <= {SMALL_SHADOW} * rng "
                f"AND lower <= {SMALL_SHADOW} * rng"),
        "means": "A session that opened at the low and closed at the high.",
        "prior": "Unknown. In India this often means the stock hit an upper circuit, "
                 "which is a liquidity fact rather than a sentiment one - and a name "
                 "locked at a circuit cannot be bought at that price anyway.",
    },
}

#: Per-bar geometry every predicate is written against.
GEOMETRY = """
CREATE OR REPLACE TEMP TABLE _kbars AS
SELECT p.lineage, p.business_date AS d,
       p.close_adj              AS c,
       e.open_price * p.factor  AS o,
       e.high_price * p.factor  AS h,
       e.low_price  * p.factor  AS l,
       p.turnover               AS tv
FROM adjusted_prices p
JOIN eod_prices e
  ON e.isin = p.isin AND e.business_date = p.business_date
 AND e.exchange = 'NSE' AND e.series = 'EQ'
WHERE p.close_adj > 0 AND e.high_price > e.low_price;

CREATE OR REPLACE TEMP TABLE _kgeom AS
SELECT *,
       ABS(c - o)                        AS body,
       h - l                             AS rng,
       h - GREATEST(o, c)                AS upper,
       LEAST(o, c) - l                   AS lower,
       LAG(c) OVER w                     AS prev_c,
       LAG(o) OVER w                     AS prev_o,
       LAG(h) OVER w                     AS prev_h,
       LAG(l) OVER w                     AS prev_l,
       LAG(c, 2) OVER w                  AS prev2_c,
       LAG(o, 2) OVER w                  AS prev2_o,
       c / NULLIF(LAG(c, 5) OVER w, 0) - 1 AS ret_5,
       ROW_NUMBER() OVER w               AS n
FROM _kbars
WINDOW w AS (PARTITION BY lineage ORDER BY d);
"""


def build(con) -> dict:
    """Detect every pattern and count how often each fires."""
    for statement in GEOMETRY.strip().split(";\n"):
        if statement.strip():
            con.execute(statement)
    cols = ",\n       ".join(
        f"CASE WHEN n >= 6 AND ({spec['sql']}) THEN 1 ELSE 0 END AS k_{name}"
        for name, spec in PATTERNS.items())
    con.execute(f"""CREATE OR REPLACE TABLE candles AS
                    SELECT lineage, d AS business_date, tv, {cols} FROM _kgeom""")
    counts = con.execute(
        "SELECT COUNT(*), " + ", ".join(f"SUM(k_{n})" for n in PATTERNS)
        + " FROM candles").fetchone()
    return {"version": VERSION, "rows": counts[0],
            "fired": dict(zip(PATTERNS, (int(x or 0) for x in counts[1:]), strict=True))}
