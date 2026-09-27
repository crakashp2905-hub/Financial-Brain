"""Decompose one name's move into market, peer, and specific, with the residual shown.

    r_stock = alpha + beta * r_market + gamma * r_peers + e

Betas come from a trailing window that **ends before the session being explained**, so the
decomposition is out of sample with respect to the move. Fitting beta on a window that includes
the day you are explaining guarantees a small residual and explains nothing - the regression
absorbs the event into the coefficient.

## The peer term, and why it is not a sector index

There is no sector classification in this archive (`security_reference` has no industry field,
`index_constituents` is empty), so a peer group cannot be looked up. It is **constructed**: the
names whose trailing returns correlate most with this one, which is an empirical peer group
rather than a taxonomic one. That has one real advantage - it groups by how names actually move
rather than by how an exchange files them - and one real limitation, which is that it cannot
distinguish "these are competitors" from "these are both mid-caps". Both are stated.

The peer return is orthogonalised against the market before entering the regression, so the
market term is not double-counted through it. Without that step beta and gamma trade off against
each other arbitrarily and neither is interpretable.

## When the decomposition is describing a data artifact

Two cases are detected and refused rather than explained. A specific return beyond
``IMPLAUSIBLE_SIGMA`` cannot be a market event - Indian equities trade under circuit limits, so a
-52 sigma move is an unadjusted corporate action, and this module found one on its first run. And
a raw move sitting exactly on a circuit band was *capped*: the observed return is a floor on the
move, so every component is scaled to a number the market was not allowed to reach. In both cases
``explainable`` is False and the news hunt is skipped, because attaching a real filing to a fake
move is how an artifact comes to look explained.

## What the residual means

The residual is the part no systematic factor explains: it is the candidate for
*company-specific news*. The module then looks for an announcement in the window that could
account for it, and reports what it found - **without asserting causation**. A filing on the same
day as a large residual is a coincidence worth showing a human, not a proven cause, and calling
it one would be exactly the confident-but-unfounded number this project spends its effort
avoiding.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from statistics import mean

#: Trailing sessions the betas are fitted on. Ends the session before the one explained.
FIT_SESSIONS = 250
#: Sessions of history required before a decomposition is attempted.
MIN_FIT = 60
#: How many empirical peers to use.
PEERS = 12
#: A residual this many trailing standard deviations from zero is worth hunting news for.
NEWS_HUNT_SIGMA = 2.0
#: Beyond this many sigmas a residual is not a market event. Indian equities trade under circuit
#: limits of 5-20% a session, so a move implying a 30-sigma company-specific shock is an
#: unadjusted corporate action - a demerger, a split, a bonus - that the adjustment factor
#: missed. Found by running this module: VEDL on 2026-04-30 decomposed to a specific return of
#: -62.36%, which is -52.7 sigma, and no news explains that because nothing in a market can.
IMPLAUSIBLE_SIGMA = 25.0
#: Circuit bands. A raw move landing on one of these to within a whisker was *capped* - the true
#: demand was larger - so the observed return is a floor on the move and not the move.
CIRCUIT_BANDS = (0.02, 0.05, 0.10, 0.20)
CIRCUIT_TOLERANCE = 0.0015


class AttributionError(ValueError):
    pass


def _ols2(y: list[float], x1: list[float], x2: list[float]) -> tuple[float, float, float]:
    """Intercept and two slopes, by normal equations on centred regressors."""
    n = len(y)
    my, m1, m2 = mean(y), mean(x1), mean(x2)
    c1 = [v - m1 for v in x1]
    c2 = [v - m2 for v in x2]
    cy = [v - my for v in y]
    s11 = sum(v * v for v in c1)
    s22 = sum(v * v for v in c2)
    s12 = sum(a * b for a, b in zip(c1, c2))
    s1y = sum(a * b for a, b in zip(c1, cy))
    s2y = sum(a * b for a, b in zip(c2, cy))
    det = s11 * s22 - s12 * s12
    if abs(det) < 1e-18 or n < 5:
        b1 = s1y / s11 if s11 > 0 else 0.0
        return my - b1 * m1, b1, 0.0
    b1 = (s22 * s1y - s12 * s2y) / det
    b2 = (s11 * s2y - s12 * s1y) / det
    return my - b1 * m1 - b2 * m2, b1, b2


def _returns(con, isin: str, end: date, sessions: int) -> dict:
    rows = con.execute("""
        SELECT business_date,
               close_adj / NULLIF(LAG(close_adj) OVER (ORDER BY business_date), 0) - 1 AS r
        FROM adjusted_prices WHERE isin = ? AND close_adj > 0
          AND business_date <= ? ORDER BY business_date DESC LIMIT ?
    """, [isin, end, sessions + 1]).fetchall()
    return {d: r for d, r in rows if r is not None and abs(r) < 0.9}


def _index_returns(con, name: str, end: date, sessions: int) -> dict:
    rows = con.execute("""
        SELECT business_date,
               close_level / NULLIF(LAG(close_level) OVER (ORDER BY business_date), 0) - 1 AS r
        FROM index_levels WHERE index_name = ? AND close_level > 0
          AND business_date <= ? ORDER BY business_date DESC LIMIT ?
    """, [name, end, sessions + 1]).fetchall()
    return {d: r for d, r in rows if r is not None}


def peers(con, isin: str, *, end: date, k: int = PEERS,
          sessions: int = FIT_SESSIONS, min_adv: float = 1e7) -> list[dict]:
    """The names this one actually moves with, over the trailing window.

    An empirical peer group, because no sector classification exists here. Restricted to names
    that trade - a correlation with something untradeable is not a peer relationship - and
    computed on the window ending at ``end``.
    """
    own = _returns(con, isin, end, sessions)
    if len(own) < MIN_FIT:
        raise AttributionError(
            f"{isin} has {len(own)} sessions before {end}; need {MIN_FIT}")
    dates = set(own)
    rows = con.execute("""
        WITH liquid AS (
            SELECT isin FROM adjusted_prices
            WHERE business_date <= ? AND business_date > CAST(? AS DATE) - INTERVAL 400 DAY
            GROUP BY isin HAVING MEDIAN(turnover) >= ? AND COUNT(*) >= ?
        )
        SELECT a.isin, a.business_date,
               a.close_adj / NULLIF(LAG(a.close_adj) OVER
                   (PARTITION BY a.isin ORDER BY a.business_date), 0) - 1 AS r
        FROM adjusted_prices a
        WHERE a.isin IN (SELECT isin FROM liquid) AND a.isin <> ?
          AND a.close_adj > 0 AND a.business_date <= ?
          AND a.business_date > CAST(? AS DATE) - INTERVAL 400 DAY
    """, [end, end, min_adv, MIN_FIT, isin, end, end]).fetchall()
    by: dict = {}
    for i, d, r in rows:
        if r is not None and abs(r) < 0.9 and d in dates:
            by.setdefault(i, {})[d] = r

    out = []
    for other, series in by.items():
        shared = sorted(set(series) & dates)
        if len(shared) < MIN_FIT:
            continue
        a = [own[d] for d in shared]
        b = [series[d] for d in shared]
        ma, mb = mean(a), mean(b)
        sa = math.sqrt(sum((v - ma) ** 2 for v in a))
        sb = math.sqrt(sum((v - mb) ** 2 for v in b))
        if sa == 0 or sb == 0:
            continue
        rho = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb)
        out.append({"isin": other, "correlation": rho, "sessions": len(shared)})
    out.sort(key=lambda p: -p["correlation"])
    return out[:k]


def explain(con, isin: str, *, on: date, index_name: str = "Nifty 500",
            sessions: int = FIT_SESSIONS, peer_list: list[dict] | None = None) -> dict:
    """Decompose ``isin``'s return on ``on`` into market, peer and specific parts.

    Betas are fitted on the ``sessions`` before ``on`` - never including it - so the number
    attributed to "company specific" is not a coefficient that swallowed the event.
    """
    own_all = _returns(con, isin, on, sessions + 2)
    if on not in own_all:
        raise AttributionError(f"{isin} has no return on {on}")
    move = own_all[on]

    fit_end = on - timedelta(days=1)
    own = _returns(con, isin, fit_end, sessions)
    mkt_all = _index_returns(con, index_name, on, sessions + 2)
    mkt = {d: v for d, v in mkt_all.items() if d <= fit_end}
    if on not in mkt_all:
        raise AttributionError(f"{index_name} has no return on {on}")

    pl = peer_list if peer_list is not None else peers(con, isin, end=fit_end,
                                                      sessions=sessions)
    pids = [p["isin"] for p in pl]
    peer_series: dict = {}
    if pids:
        rows = con.execute("""
            SELECT business_date, AVG(r) FROM (
                SELECT business_date, isin,
                       close_adj / NULLIF(LAG(close_adj) OVER
                           (PARTITION BY isin ORDER BY business_date), 0) - 1 AS r
                FROM adjusted_prices
                WHERE isin IN (SELECT UNNEST(?)) AND close_adj > 0
                  AND business_date <= ?
                  AND business_date > CAST(? AS DATE) - INTERVAL 500 DAY)
            WHERE r IS NOT NULL AND ABS(r) < 0.9 GROUP BY business_date
        """, [pids, on, on]).fetchall()
        peer_series = dict(rows)

    common = sorted(set(own) & set(mkt) & (set(peer_series) if peer_series else set(own)))
    if len(common) < MIN_FIT:
        raise AttributionError(
            f"only {len(common)} common sessions to fit on before {on}; need {MIN_FIT}")

    y = [own[d] for d in common]
    xm = [mkt[d] for d in common]
    # Orthogonalise the peer return against the market so the market term is not counted twice
    # through it. Without this, beta and gamma trade off arbitrarily and neither is meaningful.
    if peer_series:
        xp_raw = [peer_series[d] for d in common]
        _, bpm, _ = _ols2(xp_raw, xm, [0.0] * len(xm))
        mp = mean(xp_raw)
        xp = [xp_raw[i] - bpm * (xm[i] - mean(xm)) - mp for i in range(len(common))]
    else:
        bpm, xp = 0.0, [0.0] * len(common)

    alpha, beta, gamma = _ols2(y, xm, xp)
    resid = [y[i] - alpha - beta * xm[i] - gamma * xp[i] for i in range(len(common))]
    rsd = math.sqrt(sum(r * r for r in resid) / max(len(resid) - 3, 1))

    mkt_today = mkt_all[on]
    peer_today_raw = peer_series.get(on)
    peer_today = (peer_today_raw - bpm * (mkt_today - mean(xm)) - mean(
        [peer_series[d] for d in common]) if peer_today_raw is not None else 0.0)

    market_part = beta * mkt_today
    peer_part = gamma * peer_today
    drift_part = alpha
    specific = move - market_part - peer_part - drift_part

    out = {
        "isin": isin, "date": on, "move": move,
        "components": {
            "market": market_part, "peer": peer_part, "drift": drift_part,
            "specific": specific,
        },
        "checks_out": abs(move - (market_part + peer_part + drift_part + specific)) < 1e-12,
        "beta_market": beta, "beta_peer": gamma, "alpha_daily": alpha,
        "residual_sd": rsd,
        "specific_in_sigmas": specific / rsd if rsd > 0 else float("nan"),
        "fit_sessions": len(common), "fit_ends": fit_end,
        "index": index_name, "index_return": mkt_today,
        "peer_return_raw": peer_today_raw, "peers": pl[:PEERS],
        "peer_group_is_empirical": True,
    }
    # Two ways the decomposition is describing a data artifact rather than a market, both
    # detectable and both worth refusing to explain as company news.
    flags = []
    sig = out["specific_in_sigmas"]
    if rsd > 0 and abs(sig) >= IMPLAUSIBLE_SIGMA:
        flags.append(
            f"specific return of {specific:+.2%} is {sig:+.1f} sigma, beyond the "
            f"{IMPLAUSIBLE_SIGMA:.0f} sigma a market can produce under circuit limits - this is "
            f"almost certainly a corporate action the adjustment factor missed, not news")
    band = next((b for b in CIRCUIT_BANDS
                 if abs(abs(move) - b) <= CIRCUIT_TOLERANCE), None)
    if band:
        flags.append(
            f"the raw move is {move:+.2%}, on the {band:.0%} circuit band - the price was "
            f"capped, so this return is a floor on the move rather than the move, and every "
            f"component below is scaled to a number the market was not allowed to reach")
    out["data_quality_flags"] = flags
    out["explainable"] = not flags

    if flags:
        # Hunting news for an unadjusted split would attach a real filing to a fake move and
        # make the artifact look explained.
        out["candidate_news"] = {
            "found": 0, "items": [],
            "caveat": "not searched: the move itself is flagged as a data artifact"}
    elif rsd > 0 and abs(sig) >= NEWS_HUNT_SIGMA:
        out["candidate_news"] = _news(con, isin, on)
    return out


def _news(con, isin: str, on: date) -> dict:
    """Announcements that *could* account for a large residual. Not a cause - a coincidence
    worth surfacing, and labelled as one."""
    rows = con.execute("""
        SELECT business_date, event_type, materiality, headline
        FROM announcements
        WHERE isin = ? AND business_date BETWEEN CAST(? AS DATE) - INTERVAL 2 DAY
                                             AND CAST(? AS DATE)
        ORDER BY business_date DESC, materiality DESC LIMIT 10
    """, [isin, on, on]).fetchall()
    return {
        "found": len(rows),
        "items": [{"date": r[0], "event_type": r[1], "materiality": r[2],
                   "headline": (r[3] or "")[:160]} for r in rows],
        "caveat": "same-window filings, not established causes of the residual",
    }
