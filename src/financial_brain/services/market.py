"""Market-level reads: the world state, the regime, and index levels.

Every figure carries the date it was observed on. A market service that returns "VIX 14.2" without
saying when is unusable for anything point-in-time, and point-in-time is the whole discipline here.
"""
from __future__ import annotations

from datetime import date

from . import NOT_AVAILABLE, NOT_FOUND, ServiceError


def _latest_session(con, as_of: date | None) -> date:
    row = con.execute("""SELECT MAX(business_date) FROM adjusted_prices
                         WHERE (? IS NULL OR business_date <= ?)""",
                      [as_of, as_of]).fetchone()
    if not row or row[0] is None:
        raise ServiceError(NOT_AVAILABLE, "no priced sessions in the archive")
    return row[0]


def world_state(con, *, as_of: date | None = None) -> dict:
    """Everything the system knew as of one session, with each part's own as-of date.

    The parts are reported separately rather than merged because they are observed at different
    times: prices settle at the close, announcements arrive continuously, and the regime is
    classified after the session. A single "as of" over all of them would be wrong for at least
    one.
    """
    d = _latest_session(con, as_of)
    from ..regime import brain as regime_brain
    rv = regime_brain.latest(con)
    regime = con.execute("""SELECT regime FROM market_regime WHERE business_date <= ?
                            AND version = ? ORDER BY business_date DESC LIMIT 1""",
                         [d, rv]).fetchone()
    vix = con.execute("""SELECT business_date, close_level FROM index_levels
                         WHERE index_name = 'India VIX' AND business_date <= ?
                         ORDER BY business_date DESC LIMIT 1""", [d]).fetchone()
    breadth = con.execute("""
        SELECT COUNT(*) FILTER (WHERE r > 0), COUNT(*) FILTER (WHERE r < 0), COUNT(*)
        FROM (SELECT close_adj / NULLIF(LAG(close_adj) OVER
                      (PARTITION BY isin ORDER BY business_date), 0) - 1 AS r,
                     business_date
              FROM adjusted_prices WHERE business_date <= ?
                AND business_date > CAST(? AS DATE) - INTERVAL 7 DAY AND close_adj > 0)
        WHERE business_date = ? AND r IS NOT NULL
    """, [d, d, d]).fetchone()
    universe = con.execute("""SELECT COUNT(*) FROM universe_snapshots
                              WHERE business_date = (SELECT MAX(business_date)
                                  FROM universe_snapshots WHERE business_date <= ?)
                                AND exchange = 'NSE' AND instrument_type = 'STK'""",
                           [d]).fetchone()[0]
    news = con.execute("""SELECT COUNT(*), MAX(business_date) FROM announcements
                          WHERE business_date <= ?
                            AND business_date > CAST(? AS DATE) - INTERVAL 3 DAY""",
                       [d, d]).fetchone()
    up, down, total = (breadth or (0, 0, 0))
    return {
        "session": d,
        # The classifier version is reported, not assumed: market_regime is append-only and
        # v1/v2 disagree on 425 of 2,894 sessions, so "RISK_OFF" without a version is ambiguous.
        "regime": {"value": regime[0] if regime else None, "as_of": d, "version": rv},
        "india_vix": ({"value": vix[1], "as_of": vix[0]} if vix else None),
        "breadth": {"advancing": up, "declining": down, "priced": total,
                    "advance_decline": (up / down) if down else None},
        "universe_names": universe,
        "announcements_3d": {"count": news[0] if news else 0,
                             "latest": news[1] if news else None},
        "staleness_sessions": None,
    }


def indices(con, *, names: list[str] | None = None, as_of: date | None = None,
            limit: int = 40) -> list[dict]:
    """Latest level and recent change for index series, newest observation each."""
    d = _latest_session(con, as_of)
    if names:
        rows = con.execute("""
            SELECT index_name, business_date, close_level FROM index_levels
            WHERE index_name IN (SELECT UNNEST(?)) AND business_date <= ?
              AND close_level > 0
            QUALIFY ROW_NUMBER() OVER (PARTITION BY index_name
                                       ORDER BY business_date DESC) = 1
        """, [names, d]).fetchall()
        missing = set(names) - {r[0] for r in rows}
        if missing:
            raise ServiceError(NOT_FOUND, f"no levels for {sorted(missing)}",
                               available_hint="call indices() with no names to list")
    else:
        rows = con.execute("""
            SELECT index_name, business_date, close_level FROM index_levels
            WHERE business_date <= ? AND close_level > 0
            QUALIFY ROW_NUMBER() OVER (PARTITION BY index_name
                                       ORDER BY business_date DESC) = 1
            ORDER BY index_name LIMIT ?
        """, [d, limit]).fetchall()
    out = []
    for name, bd, level in rows:
        prev = con.execute("""SELECT close_level FROM index_levels
                              WHERE index_name = ? AND business_date < ? AND close_level > 0
                              ORDER BY business_date DESC LIMIT 1""",
                           [name, bd]).fetchone()
        out.append({"index": name, "as_of": bd, "level": level,
                    "change": (level / prev[0] - 1) if prev and prev[0] else None})
    return out


def regime_history(con, *, start: date | None = None, end: date | None = None,
                   version: str | None = None) -> dict:
    """Regime classification over a window, with how long the archive has held each."""
    from ..regime import brain as regime_brain
    rv = version or regime_brain.latest(con)
    # Without the version filter this counted every session twice and reported 5,788 sessions
    # for a 2,903-session archive.
    rows = con.execute("""
        SELECT regime, COUNT(*), MIN(business_date), MAX(business_date)
        FROM market_regime
        WHERE version = ?
          AND (? IS NULL OR business_date >= ?) AND (? IS NULL OR business_date <= ?)
        GROUP BY regime ORDER BY COUNT(*) DESC
    """, [rv, start, start, end, end]).fetchall()
    if not rows:
        raise ServiceError(NOT_AVAILABLE, "no regime classifications in that window")
    total = sum(r[1] for r in rows)
    return {"window": {"start": start, "end": end}, "version": rv,
            "sessions": total,
            "regimes": [{"regime": r[0], "sessions": r[1], "share": r[1] / total,
                         "first": r[2], "last": r[3]} for r in rows]}
