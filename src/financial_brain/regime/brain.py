"""Market Regime Brain (C07) - what kind of market is this?

Deterministic and versioned. Every session gets a regime and the reasons for it, so a
downstream decision can say *which* rules made the market "risk-off", and a rule change
produces a new ``version`` rather than silently rewriting history.

Inputs, all Tier-1 and all held since 2015:
    Nifty 50 (trend, drawdown, realised volatility) - index_levels_canonical
    India VIX (implied volatility)                  - index_levels_canonical
    Breadth: share of NSE EQ stocks above their own 200/50-session averages, and the
    day's advances/declines                         - universe_snapshots

Regimes, checked in this order:

``CRISIS``    fear is acute: VIX >= 30, or 20-session realised vol >= 35% annualised,
              or a fall of >= 10% in 20 sessions from a >= 15% drawdown
``RISK_OFF``  the trend is broken: below the 200-session average with that average
              falling, or breadth below 35%, or a drawdown of 10% or more
``NARROW``    the index trend is healthy but the market underneath is not: Nifty 50
              above a 200-session average with 50 above 200, yet fewer than 35% of
              stocks above their own. The 2018-19 pattern - large caps making highs
              while small and mid caps fell ~40% - which RISK_OFF would misdescribe
``RISK_ON``   a healthy trend: above the 200-session average, 50 above 200, breadth
              >= 50%, VIX < 25
``NEUTRAL``   everything else

Versions: v1 used VIX < 20 for RISK_ON and had no NARROW. Validation against 2015-2026
showed VIX stayed 20-25 through the 2021 bull (post-COVID baseline), and that 2019 - a
year of new Nifty highs - read as 225 sessions of RISK_OFF. v2 fixes both. v1's rows are
kept: a rule change adds a version, it does not rewrite history.

Hysteresis: a change of regime is adopted only after ``CONFIRM`` consecutive sessions
indicate it - otherwise a market hovering at its 200-session average flips daily. CRISIS
is the exception and is adopted immediately: nobody should wait three days to be told
the market is crashing.
"""
from __future__ import annotations

from datetime import datetime, timezone

VERSION = "v2"


#: The version every reader must pin to. ``market_regime`` is deliberately append-only - a rule
#: change adds a version rather than rewriting history - so the table holds **every** version at
#: once, and an unversioned read returns two rows per session.
#:
#: That is not a harmless duplicate. `v1` and `v2` disagree on the regime for **425 of 2,894
#: sessions (14.7%)**, mostly RISK_OFF against NARROW, and eleven modules were reading the table
#: with no version filter. The common pattern
#:
#:     dict(con.execute("SELECT business_date, regime FROM market_regime").fetchall())
#:
#: silently keeps whichever row the scan happened to yield last, so the firewall's regime gate,
#: the time-series harness and the stress engine were each reading a non-deterministic label on a
#: seventh of the archive. Same class of defect as an unordered NTILE tie-break: an unspecified
#: choice resolved by scan order.
#:
#: ``latest`` reads the newest version present rather than hard-coding ``VERSION``, so a database
#: built by an older release still answers, and ``pinned`` is the SQL fragment to use.
def latest(con) -> str:
    row = con.execute("SELECT MAX(version) FROM market_regime").fetchone()
    return (row[0] if row and row[0] else VERSION)


def pinned(con) -> tuple[str, str]:
    """``(sql_fragment, version)`` - append the fragment to a WHERE clause on market_regime."""
    v = latest(con)
    return f"version = '{v}'", v


def series(con, *, version: str | None = None) -> dict:
    """``business_date -> regime`` for exactly one version. The accessor readers should use."""
    v = version or latest(con)
    return dict(con.execute(
        "SELECT business_date, regime FROM market_regime WHERE version = ?", [v]).fetchall())


CONFIRM = 3

T = {  # thresholds - changing any of these means a new VERSION
    "crisis_vix": 30.0, "crisis_rv20": 0.35, "crisis_dd": -0.15, "crisis_ret20": -0.10,
    "off_breadth200": 0.35, "off_dd": -0.10,
    "on_breadth200": 0.50, "on_vix": 25.0,
}

BREADTH_SQL = """
CREATE OR REPLACE TABLE market_breadth AS
WITH px AS (
    SELECT business_date, isin, close_price
    FROM universe_snapshots
    WHERE exchange = 'NSE' AND series = 'EQ' AND close_price > 0
), ma AS (
    SELECT business_date, isin, close_price,
           AVG(close_price) OVER w200 AS ma200, COUNT(*) OVER w200 AS n200,
           AVG(close_price) OVER w50 AS ma50, COUNT(*) OVER w50 AS n50,
           LAG(close_price) OVER (PARTITION BY isin ORDER BY business_date) AS prev
    FROM px
    WINDOW w200 AS (PARTITION BY isin ORDER BY business_date
                    ROWS BETWEEN 199 PRECEDING AND CURRENT ROW),
           w50  AS (PARTITION BY isin ORDER BY business_date
                    ROWS BETWEEN 49 PRECEDING AND CURRENT ROW)
)
SELECT business_date,
       COUNT(*) FILTER (WHERE n200 = 200)                                  AS n_200,
       AVG(CASE WHEN n200 = 200 THEN (close_price > ma200)::INT END)       AS above_200,
       AVG(CASE WHEN n50 = 50 THEN (close_price > ma50)::INT END)          AS above_50,
       COUNT(*) FILTER (WHERE prev IS NOT NULL AND close_price > prev)     AS advances,
       COUNT(*) FILTER (WHERE prev IS NOT NULL AND close_price < prev)     AS declines
FROM ma GROUP BY business_date
"""

FEATURES_SQL = """
WITH n AS (
    SELECT business_date, close_level AS c FROM index_levels_canonical
    WHERE index_name = 'Nifty 50' AND variant = 'PRICE'
), v AS (
    SELECT business_date, close_level AS vix FROM index_levels_canonical
    WHERE index_name = 'India VIX' AND variant = 'PRICE'
), f AS (
    SELECT n.business_date, n.c,
           AVG(c) OVER (ORDER BY business_date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS ma200,
           COUNT(*) OVER (ORDER BY business_date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS k200,
           AVG(c) OVER (ORDER BY business_date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS ma50,
           MAX(c) OVER (ORDER BY business_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS hi252,
           c / LAG(c, 20) OVER (ORDER BY business_date) - 1 AS ret20,
           LN(c / LAG(c) OVER (ORDER BY business_date)) AS lr
    FROM n
), g AS (
    SELECT *, STDDEV_SAMP(lr) OVER (ORDER BY business_date
                                    ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) * SQRT(252) AS rv20,
           ma200 / LAG(ma200, 20) OVER (ORDER BY business_date) - 1 AS ma200_slope
    FROM f
)
SELECT g.business_date, g.c, g.ma50, g.ma200, g.k200, g.ma200_slope, g.c / g.hi252 - 1 AS dd,
       g.ret20, g.rv20, v.vix, b.above_200, b.above_50, b.advances, b.declines
FROM g LEFT JOIN v USING (business_date) LEFT JOIN market_breadth b USING (business_date)
ORDER BY g.business_date
"""


def indicate(f: dict) -> tuple[str, list[str]]:
    """The regime one session's features indicate, with the reasons that fired."""
    vix, rv, dd, r20 = f["vix"], f["rv20"], f["dd"], f["ret20"]
    crisis = []
    if vix is not None and vix >= T["crisis_vix"]:
        crisis.append(f"India VIX {vix:.1f} >= {T['crisis_vix']:.0f}")
    if rv is not None and rv >= T["crisis_rv20"]:
        crisis.append(f"20-session realised vol {rv:.0%} >= {T['crisis_rv20']:.0%}")
    if dd is not None and r20 is not None and dd <= T["crisis_dd"] and r20 <= T["crisis_ret20"]:
        crisis.append(f"drawdown {dd:.0%} with a {r20:.0%} fall in 20 sessions")
    if crisis:
        return "CRISIS", crisis

    c, ma50, ma200, b200 = f["c"], f["ma50"], f["ma200"], f["above_200"]
    full = f["k200"] == 200
    index_healthy = full and c > ma200 and ma50 > ma200
    weak_breadth = b200 is not None and b200 < T["off_breadth200"]
    if index_healthy and weak_breadth and (dd is None or dd > T["off_dd"]):
        return "NARROW", [f"Nifty 50 in a healthy trend (50 > 200) but only {b200:.0%} of "
                          f"NSE stocks above their 200-session average"]
    off = []
    if full and c < ma200 and (f["ma200_slope"] or 0) < 0:
        off.append("Nifty 50 below a falling 200-session average")
    if weak_breadth:
        off.append(f"only {b200:.0%} of NSE stocks above their 200-session average")
    if dd is not None and dd <= T["off_dd"]:
        off.append(f"Nifty 50 {dd:.0%} below its 52-week high")
    if off:
        return "RISK_OFF", off

    if (full and c > ma200 and ma50 > ma200 and b200 is not None
            and b200 >= T["on_breadth200"] and vix is not None and vix < T["on_vix"]):
        return "RISK_ON", [f"Nifty 50 above a rising trend (50 > 200), {b200:.0%} of "
                           f"stocks above their 200-session average, VIX {vix:.1f}"]
    return "NEUTRAL", ["no risk-on or risk-off condition met"]


def classify_history(rows: list[dict]) -> list[dict]:
    """Apply ``indicate`` to every session, with hysteresis."""
    out, current, pending, streak = [], None, None, 0
    for f in rows:
        raw, reasons = indicate(f)
        if current is None or raw == "CRISIS":
            current, pending, streak = raw, None, 0
        elif raw != current:
            streak = streak + 1 if raw == pending else 1
            pending = raw
            if streak >= CONFIRM:
                current, pending, streak = raw, None, 0
        else:
            pending, streak = None, 0
        why = "; ".join(reasons)
        if raw != current:
            # Held by hysteresis: say so, rather than explain a regime the day's own
            # signals do not support.
            why = (f"holding {current}: today alone indicates {raw} ({why}); a change "
                   f"needs {CONFIRM} consecutive sessions")
        out.append({**f, "raw_regime": raw, "regime": current, "reasons": why})
    return out


def build(con) -> dict:
    """Recompute breadth, features and regimes for the whole history."""
    from ..indices import lineage
    lineage.record(con)
    con.execute(lineage.CANONICAL_VIEW)
    con.execute(BREADTH_SQL)
    cur = con.execute(FEATURES_SQL)
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    hist = classify_history(rows)
    now = datetime.now(timezone.utc)
    con.execute("DELETE FROM market_regime WHERE version = ?", [VERSION])
    con.executemany(
        """INSERT INTO market_regime (business_date, version, regime, raw_regime, reasons,
           nifty_close, ma50, ma200, drawdown, ret20, rv20, vix, breadth_200, breadth_50,
           advances, declines, computed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [[h["business_date"], VERSION, h["regime"], h["raw_regime"], h["reasons"], h["c"],
          h["ma50"], h["ma200"], h["dd"], h["ret20"], h["rv20"], h["vix"], h["above_200"],
          h["above_50"], h["advances"], h["declines"], now] for h in hist])
    return {"sessions": len(hist)}
