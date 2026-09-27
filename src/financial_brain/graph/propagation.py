"""Does an event at one company move its group's other companies? Measured, not assumed.

The usual event-propagation engine is a graph of hand-assigned edges - supplier, customer,
competitor - each carrying a confidence weight somebody chose, and a shock is pushed through it.
The output is a number with no measurement behind it anywhere: not in the edge, not in the weight,
not in the decay. It is an opinion with a diagram.

This archive holds exactly one relationship type that is **sourced**: promoter-group membership,
from shareholding disclosures. So that is the edge this measures, and the claim is narrow and
testable:

    when company A files a high-materiality event, do the *other* listed companies in A's
    promoter group move abnormally over the following sessions?

Abnormal means net of market and of the group member's own beta, fitted on a window ending before
the event. If the answer is no, an elaborate propagation graph built on softer edges than this one
is not going to work either, and that is worth knowing before building it.

## Why promoter groups are the right test case for India specifically

Indian listed companies cluster into promoter groups far more than in most markets, groups share
management, funding and reputation, and a governance event at one member is routinely information
about the others. If contagion exists anywhere in this data it should exist here, which makes this
the strongest available test of the propagation idea rather than a convenient one.

## Clustering, which is not optional here

Siblings of the same event share the event, so their abnormal returns are **not independent
draws**. A promoter group with 16 listed members contributes 16 near-identical observations from
one filing and inflates a naive t-statistic by roughly sqrt(16). The first run of this module
reported a sibling t of -8.31 at 20 sessions from 1,074 "observations" that were really 372
events.

So the reported statistic clusters by event - one observation per filing, the mean across that
filing's siblings - and the naive figure is kept beside it only so the size of the correction is
visible.

## What this cannot do

It cannot measure supplier, customer or competitor propagation, because those edges do not exist
in this archive and inventing them would produce exactly the unfounded confident number the rest
of this project is built to avoid. Sector propagation is also unavailable - there is no industry
classification here, and `index_constituents` has zero rows.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from statistics import mean, stdev

#: Trailing sessions to fit each member's market beta on, ending before the event.
FIT_SESSIONS = 250
MIN_FIT = 60
#: Sessions after the event over which abnormal return is accumulated.
HORIZONS = (1, 3, 5, 20)
#: A group must have at least this many *other* listed members for the question to mean anything.
MIN_SIBLINGS = 1
#: Events below this many observations are reported and not interpreted.
MIN_EVENTS = 30


def _index(con, name: str, start: date, end: date) -> dict:
    rows = con.execute("""
        SELECT business_date,
               close_level / NULLIF(LAG(close_level) OVER (ORDER BY business_date), 0) - 1
        FROM index_levels WHERE index_name = ? AND close_level > 0
          AND business_date BETWEEN ? AND ? ORDER BY business_date
    """, [name, start, end]).fetchall()
    return {d: r for d, r in rows if r is not None}


def group_of(con) -> dict[str, str]:
    """``isin -> group_id``, for clustering at the group level."""
    return dict(con.execute("""SELECT member, MIN(group_id) FROM promoter_groups
                               WHERE member IS NOT NULL GROUP BY member""").fetchall())


def siblings(con) -> dict[str, list[str]]:
    """``isin -> the other listed ISINs in its promoter group``.

    Groups are keyed on ``group_id`` from ``promoter_groups``, which is derived from
    shareholding disclosures rather than assigned.
    """
    rows = con.execute("""
        SELECT g.group_id, g.member FROM promoter_groups g
        WHERE g.member IS NOT NULL
    """).fetchall()
    by_group: dict[str, set] = {}
    for gid, member in rows:
        by_group.setdefault(gid, set()).add(member)
    out: dict[str, list[str]] = {}
    for members in by_group.values():
        for m in members:
            out.setdefault(m, [])
            out[m].extend(x for x in members if x != m)
    return {k: sorted(set(v)) for k, v in out.items() if v}


def events(con, *, start: date, end: date, materiality: str = "high") -> list[dict]:
    """High-materiality filings that have at least one group sibling to propagate to."""
    sib = siblings(con)
    if not sib:
        return []
    rows = con.execute("""
        SELECT a.isin, a.business_date, a.event_type
        FROM announcements a
        WHERE a.materiality = ? AND a.isin IN (SELECT UNNEST(?))
          AND a.business_date BETWEEN ? AND ?
          AND a.event_type IS NOT NULL
        ORDER BY a.business_date
    """, [materiality, list(sib), start, end]).fetchall()
    return [{"isin": r[0], "date": r[1], "event_type": r[2],
             "siblings": sib[r[0]]} for r in rows
            if len(sib.get(r[0], [])) >= MIN_SIBLINGS]


def _abnormal(con, isin: str, event_day: date, index: dict, horizon: int) -> float | None:
    """Cumulative abnormal return over ``horizon`` sessions **after** the event day.

    The position is formed on the session *after* the filing, never on it: a filing published
    during or after the session it is dated cannot be traded at that session's close, and
    assuming otherwise is the cheapest look-ahead in event studies.
    """
    rows = con.execute("""
        SELECT business_date,
               close_adj / NULLIF(LAG(close_adj) OVER (ORDER BY business_date), 0) - 1 AS r
        FROM adjusted_prices
        WHERE isin = ? AND close_adj > 0
          AND business_date BETWEEN CAST(? AS DATE) - INTERVAL 500 DAY
                                AND CAST(? AS DATE) + INTERVAL 90 DAY
        ORDER BY business_date
    """, [isin, event_day, event_day]).fetchall()
    series = [(d, r) for d, r in rows if r is not None and abs(r) < 0.5]
    if not series:
        return None
    fit = [(d, r) for d, r in series if d < event_day][-FIT_SESSIONS:]
    fwd = [(d, r) for d, r in series if d > event_day][:horizon]
    if len(fit) < MIN_FIT or len(fwd) < horizon:
        return None
    pairs = [(r, index[d]) for d, r in fit if d in index]
    if len(pairs) < MIN_FIT:
        return None
    ys = [p[0] for p in pairs]
    xs = [p[1] for p in pairs]
    mx, my = mean(xs), mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    beta = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    alpha = my - beta * mx
    ar = 0.0
    for d, r in fwd:
        if d not in index:
            return None
        ar += r - alpha - beta * index[d]
    return ar


def _t(xs) -> float:
    return (mean(xs) / stdev(xs) * math.sqrt(len(xs))
            if len(xs) > 2 and stdev(xs) > 0 else float("nan"))


def measure(con, *, start: date, end: date, index_name: str = "Nifty 500",
            horizons=HORIZONS, limit: int | None = None) -> dict:
    """Abnormal returns of group siblings after a high-materiality filing at one member.

    Reports the filing company's own abnormal return alongside the siblings', because the
    comparison is the point: if the event moves the filer and not the siblings, information does
    not travel along this edge, and no propagation graph built on softer edges will do better.
    """
    idx = _index(con, index_name, start - timedelta(days=760), end + timedelta(days=120))
    if len(idx) < MIN_FIT:
        return {"note": f"only {len(idx)} sessions of {index_name!r}"}
    evs = events(con, start=start, end=end)
    if limit:
        evs = evs[:limit]

    gmap = group_of(con)
    per_h: dict = {h: {"filer": [], "sibling": [], "clustered": [], "by_group": {},
                       "by_event_type": {}, "by_event_type_clustered": {}}
                   for h in horizons}
    used = 0
    for e in evs:
        got_any = False
        for h in horizons:
            f = _abnormal(con, e["isin"], e["date"], idx, h)
            if f is not None:
                per_h[h]["filer"].append(f)
                got_any = True
            sibs = []
            for s in e["siblings"]:
                a = _abnormal(con, s, e["date"], idx, h)
                if a is not None:
                    sibs.append(a)
                    per_h[h]["sibling"].append(a)
                    per_h[h]["by_event_type"].setdefault(e["event_type"], []).append(a)
                    got_any = True
            if sibs:
                # **One observation per event**, not per sibling. Siblings of the same event share
                # the event, so their abnormal returns are not independent draws - a group with
                # 16 listed members contributes 16 near-identical observations and inflates the
                # t-statistic by roughly sqrt(16). Clustering by event is the standard treatment
                # and it is the number to read.
                m = mean(sibs)
                per_h[h]["clustered"].append(m)
                per_h[h]["by_group"].setdefault(gmap.get(e["isin"], e["isin"]),
                                                []).append(m)
                per_h[h]["by_event_type_clustered"].setdefault(
                    e["event_type"], []).append(m)
        used += 1 if got_any else 0

    out = {"index": index_name, "events_considered": len(evs), "events_used": used,
           "window": (start, end), "horizons": {}}
    for h in horizons:
        fil, sib, clus = per_h[h]["filer"], per_h[h]["sibling"], per_h[h]["clustered"]
        row = {
            "filer_n": len(fil), "filer_mean_ar": mean(fil) if fil else None,
            "filer_t": _t(fil) if fil else None,
            "sibling_n": len(sib), "sibling_mean_ar": mean(sib) if sib else None,
            # Naive t, kept only so the size of the clustering correction is visible. It treats
            # correlated siblings of one event as independent and is not the number to quote.
            "sibling_t_naive": _t(sib) if sib else None,
            "events_n": len(clus),
            "sibling_mean_ar_clustered": mean(clus) if clus else None,
            "sibling_t": _t(clus) if clus else None,
            "siblings_per_event": (len(sib) / len(clus)) if clus else None,
            "interpretable": len(clus) >= MIN_EVENTS,
        }
        # A third level, because clustering by event is still not enough at long horizons. Two
        # filings ten days apart in the same group produce **overlapping** 20-session windows
        # over the same names, so their abnormal returns are correlated across events as well as
        # within them. Collapsing to one observation per *group* removes both, at the cost of
        # most of the sample - which is the trade every clustering choice makes, and the three
        # numbers together say how much the conclusion depends on it.
        groups = per_h[h]["by_group"]
        gmeans = [mean(v) for v in groups.values()]
        row["groups_n"] = len(gmeans)
        row["sibling_mean_ar_by_group"] = mean(gmeans) if gmeans else None
        row["sibling_t_by_group"] = _t(gmeans) if gmeans else None
        row["events_per_group"] = (len(clus) / len(gmeans)) if gmeans else None
        if len(clus) < MIN_EVENTS:
            row["why_not"] = (f"{len(clus)} independent events, need {MIN_EVENTS}; "
                              f"{len(sib)} sibling observations is not {len(sib)} "
                              f"independent observations")
        row["by_event_type"] = {
            k: {"n_siblings": len(per_h[h]["by_event_type"].get(k, [])),
                "n_events": len(v), "mean_ar": mean(v), "t": _t(v),
                "interpretable": len(v) >= MIN_EVENTS}
            for k, v in sorted(per_h[h]["by_event_type_clustered"].items(),
                               key=lambda kv: -len(kv[1]))
        }
        out["horizons"][h] = row
    return out
