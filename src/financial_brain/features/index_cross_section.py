"""Point-in-time index breadth, membership concentration and index co-movement.

All memberships and TRI observations are filtered by both their effective session/date
and their recorded observation time. Membership statistics use current-on-that-date
constituents only; constituent weights are not published in ``index_membership``, so
concentration is explicitly by industry member counts, not market-cap weights.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any


class IndexFeatureError(ValueError):
    """Invalid decision time or cross-index feature configuration."""


def _table(con: Any, name: str) -> bool:
    return bool(con.execute("SELECT 1 FROM information_schema.tables WHERE table_name = ?", [name]).fetchone())


def _correlation(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    dx, dy = [x - mx for x in xs], [y - my for y in ys]
    xx, yy = sum(x * x for x in dx), sum(y * y for y in dy)
    if xx <= 0 or yy <= 0:
        return None
    return sum(x * y for x, y in zip(dx, dy, strict=True)) / math.sqrt(xx * yy)


def _tri_series(con: Any, names: set[str], session: date, decision_at: datetime) -> dict[str, dict[date, float]]:
    if not _table(con, "index_total_return"):
        return {}
    rows = con.execute("""SELECT index_name, business_date, tri
                          FROM index_total_return
                          WHERE index_name IN (SELECT UNNEST(?))
                            AND business_date <= ? AND observed_at <= ?
                            AND tri > 0
                          ORDER BY index_name, business_date""",
                       [sorted(names), session, decision_at]).fetchall()
    out: defaultdict[str, dict[date, float]] = defaultdict(dict)
    for name, day, tri in rows:
        out[name][day] = float(tri)
    return dict(out)


def build_index_features(
    con: Any,
    *,
    session: date,
    decision_at: datetime,
    correlation_lookback: int = 60,
    relative_strength_lookback: int = 20,
    max_membership_age: timedelta = timedelta(days=45),
    min_member_coverage: float = 0.70,
) -> dict[str, Any]:
    """Compute aligned cross-index features as they were observable at ``decision_at``.

    Breadth is calculated for NIFTY 50 and NIFTY BANK membership snapshots. Co-movement
    is the Pearson correlation of their daily gross TRI returns on shared dates. Sector
    relative strength compares each observed NIFTY sector TRI with NIFTY 50 over common
    endpoints. If a historical membership or TRI value had not yet been observed by the
    decision time, it is not included.
    """
    if decision_at.tzinfo is None or decision_at.utcoffset() is None:
        raise IndexFeatureError("decision_at must be timezone-aware")
    if correlation_lookback < 2 or relative_strength_lookback < 1:
        raise IndexFeatureError("lookbacks must be positive and correlation needs at least 2")
    if max_membership_age < timedelta(0) or not 0 < min_member_coverage <= 1:
        raise IndexFeatureError("membership age and coverage threshold are invalid")

    result: dict[str, Any] = {
        "status": "AVAILABLE",
        "session": session.isoformat(),
        "decision_at": decision_at.astimezone(timezone.utc).isoformat(),
        "membership": {},
        "constituent_sector_concentration": {},
        "nifty_bank_correlation": {"value": None, "observations": 0},
        "sector_relative_strength": [],
        "alignment": "exact shared session dates; source observed_at must be <= decision_at",
    }
    if not _table(con, "index_membership"):
        result.update(status="UNAVAILABLE", reason="INDEX_MEMBERSHIP_TABLE_MISSING")
        return result

    membership_rows = con.execute("""WITH latest AS (
            SELECT index_name, MAX(as_of) AS snapshot_date
            FROM index_membership
            WHERE index_name IN ('NIFTY 50', 'NIFTY BANK')
              AND as_of <= ? AND observed_at <= ?
            GROUP BY index_name
        )
        SELECT m.index_name, m.as_of, m.symbol, m.isin, m.industry, m.observed_at
        FROM index_membership m JOIN latest l
          ON m.index_name = l.index_name AND m.as_of = l.snapshot_date
        WHERE m.observed_at <= ?
        ORDER BY m.index_name, m.isin""", [session, decision_at, decision_at]).fetchall()
    by_index: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for name, snapshot_date, symbol, isin, industry, observed_at in membership_rows:
        if not isin:
            continue
        by_index[name].append({"symbol": symbol, "isin": isin, "industry": industry,
                               "snapshot_date": snapshot_date, "observed_at": observed_at})

    if not _table(con, "features") or not _table(con, "adjusted_prices"):
        result.update(status="UNAVAILABLE", reason="EQUITY_FEATURES_MISSING")
        return result
    for name in ("NIFTY 50", "NIFTY BANK"):
        members = by_index.get(name, [])
        snapshot_date = members[0]["snapshot_date"] if members else None
        stale = snapshot_date is not None and session - snapshot_date > max_membership_age
        available = bool(members) and not stale
        if available:
            assert snapshot_date is not None
            member_isins = list(dict.fromkeys(member["isin"] for member in members))
            rows = con.execute("""SELECT m.isin, f.ret_20d, f.above_ma200
                FROM (SELECT UNNEST(?) AS isin) m
                LEFT JOIN adjusted_prices p ON p.isin = m.isin AND p.business_date = ?
                LEFT JOIN features f ON f.lineage = p.lineage AND f.business_date = ?""",
                               [member_isins, session, session]).fetchall()
            known = [(ret, above) for _, ret, above in rows if ret is not None]
            breadth_200 = [bool(above) for ret, above in known if above is not None]
            industries = [str(m["industry"]).strip() for m in members if m["industry"] and str(m["industry"]).strip()]
            counts = Counter(industries)
            sector_total = sum(counts.values())
            sector_hhi = sum((n / sector_total) ** 2 for n in counts.values()) if sector_total else None
            coverage = len(known) / len(member_isins) if member_isins else 0.0
            sufficient = coverage >= min_member_coverage
            result["membership"][name] = {
                "snapshot_date": snapshot_date.isoformat(),
                "members": len(member_isins),
                "matched_members": len(known),
                "coverage": coverage,
                "pct_up_20d": sum(float(ret > 0) for ret, _ in known) / len(known) if sufficient and known else None,
                "pct_above_ma200": sum(breadth_200) / len(breadth_200) if sufficient and breadth_200 else None,
                "status": "AVAILABLE" if sufficient else "LOW_MEMBER_COVERAGE",
            }
            result["constituent_sector_concentration"][name] = {
                "basis": "equal constituent counts by source industry; not index weights",
                "classified_members": sector_total,
                "industry_count": len(counts),
                "largest_industry_share": max(counts.values()) / sector_total if sector_total else None,
                "industry_hhi": sector_hhi,
                "status": "AVAILABLE" if sector_total else "INDUSTRY_UNAVAILABLE",
            }
        else:
            result["membership"][name] = {
                "snapshot_date": snapshot_date.isoformat() if snapshot_date else None,
                "members": len(members), "matched_members": 0, "coverage": 0.0,
                "pct_up_20d": None, "pct_above_ma200": None,
                "status": "STALE_MEMBERSHIP" if stale else "MEMBERSHIP_UNAVAILABLE",
            }
            result["constituent_sector_concentration"][name] = {
                "basis": "equal constituent counts by source industry; not index weights",
                "classified_members": 0, "industry_count": 0, "largest_industry_share": None,
                "industry_hhi": None, "status": "MEMBERSHIP_UNAVAILABLE",
            }

    series = _tri_series(con, {"NIFTY 50", "NIFTY BANK"}, session, decision_at)
    a, b = series.get("NIFTY 50", {}), series.get("NIFTY BANK", {})
    common = sorted(set(a) & set(b))
    if len(common) >= correlation_lookback + 1:
        common = common[-(correlation_lookback + 1):]
        ar = [a[y] / a[x] - 1 for x, y in zip(common[:-1], common[1:], strict=True)]
        br = [b[y] / b[x] - 1 for x, y in zip(common[:-1], common[1:], strict=True)]
        result["nifty_bank_correlation"] = {
            "value": _correlation(ar, br), "observations": len(ar),
            "first_session": common[0].isoformat(), "last_session": common[-1].isoformat(),
        }
    else:
        result["nifty_bank_correlation"] = {"value": None, "observations": max(len(common) - 1, 0),
                                             "status": "INSUFFICIENT_ALIGNED_TRI_HISTORY"}

    all_indices = con.execute("""SELECT DISTINCT index_name FROM index_total_return
                                 WHERE index_name LIKE 'NIFTY %'""").fetchall() if _table(con, "index_total_return") else []
    sector_names = {r[0] for r in all_indices} - {"NIFTY 50", "NIFTY 500", "NIFTY BANK"}
    sector_series = _tri_series(con, sector_names | {"NIFTY 50"}, session, decision_at)
    nifty = sector_series.get("NIFTY 50", {})
    for name in sorted(sector_names):
        values = sector_series.get(name, {})
        aligned = sorted(set(values) & set(nifty))
        if len(aligned) <= relative_strength_lookback:
            result["sector_relative_strength"].append({
                "index": name, "relative_return": None,
                "observations": max(len(aligned) - 1, 0), "status": "INSUFFICIENT_ALIGNED_TRI_HISTORY",
            })
            continue
        aligned = aligned[-(relative_strength_lookback + 1):]
        start, end = aligned[0], aligned[-1]
        relative = (values[end] / values[start] - 1) - (nifty[end] / nifty[start] - 1)
        result["sector_relative_strength"].append({
            "index": name, "relative_return": relative,
            "observations": len(aligned) - 1,
            "first_session": start.isoformat(), "last_session": end.isoformat(),
            "status": "AVAILABLE",
        })
    return result


__all__ = ["IndexFeatureError", "build_index_features"]
