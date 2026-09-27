"""Company-level reads: identity, price history, events, and why a move happened.

Identity is resolved through ISIN rather than ticker, because tickers are reused and renamed while
an ISIN is the thing corporate actions and lineage are keyed on. A ticker lookup is offered and it
returns *every* match with its dates rather than guessing which one was meant.
"""
from __future__ import annotations

from datetime import date

from ..attribution import move
from . import BAD_REQUEST, NOT_ESTIMABLE, NOT_FOUND, ServiceError


def resolve(con, query: str, *, as_of: date | None = None) -> list[dict]:
    """ISINs matching an ISIN, ticker or company-name fragment.

    Returns every match, best first: exact ISIN, exact ticker, ticker prefix, then name. A ticker
    has been more than one company often enough in this market that silently picking the most
    recent is how the wrong company gets analysed - and ordering by recency alone put Reliance
    Chemotex above Reliance Industries for "RELIANCE".
    """
    q = (query or "").strip().upper()
    if len(q) < 2:
        raise ServiceError(BAD_REQUEST, "give at least two characters")
    rows = con.execute("""
        SELECT DISTINCT l.isin, l.ticker, l.first_seen, l.last_seen,
               (SELECT MAX(r.company_name) FROM security_reference r WHERE r.isin = l.isin)
        FROM security_listings l
        WHERE UPPER(l.isin) = ? OR UPPER(l.ticker) = ? OR UPPER(l.ticker) LIKE ?
           OR EXISTS (SELECT 1 FROM security_reference r WHERE r.isin = l.isin
                        AND UPPER(r.company_name) LIKE ?)
        -- Exact ISIN, then exact ticker, then prefix, then name. Ordering by recency alone put
        -- Reliance Chemotex above Reliance Industries for the query "RELIANCE", which is exactly
        -- the wrong-company-analysed failure this function exists to prevent.
        ORDER BY CASE WHEN UPPER(l.isin) = ? THEN 0
                      WHEN UPPER(l.ticker) = ? THEN 1
                      WHEN UPPER(l.ticker) LIKE ? THEN 2
                      ELSE 3 END,
                 l.last_seen DESC NULLS LAST
        LIMIT 25
    """, [q, q, f"{q}%", f"%{q}%", q, q, f"{q}%"]).fetchall()
    if not rows:
        raise ServiceError(NOT_FOUND, f"nothing matches {query!r}")
    del as_of
    return [{"isin": r[0], "ticker": r[1], "first_seen": r[2], "last_seen": r[3],
             "company_name": r[4]} for r in rows]


def overview(con, isin: str, *, as_of: date | None = None) -> dict:
    """What is known about one company as of a date, with each part's own observation date."""
    d = as_of or con.execute("SELECT MAX(business_date) FROM adjusted_prices").fetchone()[0]
    ident = con.execute("""
        SELECT (SELECT MAX(ticker) FROM security_listings WHERE isin = ?),
               (SELECT MAX(company_name) FROM security_reference WHERE isin = ?),
               (SELECT MIN(listing_date) FROM security_reference WHERE isin = ?),
               (SELECT MIN(anchor) FROM promoter_groups WHERE member = ?),
               (SELECT MIN(group_id) FROM promoter_groups WHERE member = ?)
    """, [isin, isin, isin, isin, isin]).fetchone()
    px = con.execute("""SELECT business_date, close_adj, turnover FROM adjusted_prices
                        WHERE isin = ? AND business_date <= ? AND close_adj > 0
                        ORDER BY business_date DESC LIMIT 1""", [isin, d]).fetchone()
    if not px:
        raise ServiceError(NOT_FOUND, f"no priced sessions for {isin} at or before {d}")
    feats = con.execute("""
        SELECT f.business_date, f.mom_12_1, f.dist_52w_high, f.vol_60, f.ret_20d, f.adv20,
               f.rsi_14, f.above_ma200
        FROM features f JOIN security_lineage l ON l.lineage = f.lineage
        WHERE l.isin = ? AND f.business_date <= ?
        ORDER BY f.business_date DESC LIMIT 1
    """, [isin, d]).fetchone()
    events = con.execute("""
        SELECT business_date, event_type, materiality, headline FROM announcements
        WHERE isin = ? AND business_date <= ?
        ORDER BY business_date DESC LIMIT 10
    """, [isin, d]).fetchall()
    return {
        "isin": isin, "as_of": d,
        "identity": {"ticker": ident[0], "company_name": ident[1],
                     "listing_date": ident[2], "promoter_group": ident[3],
                     "promoter_group_id": ident[4]},
        "price": {"as_of": px[0], "close_adj": px[1], "turnover": px[2]},
        "features": ({"as_of": feats[0], "mom_12_1": feats[1], "dist_52w_high": feats[2],
                      "vol_60": feats[3], "ret_20d": feats[4], "adv20": feats[5],
                      "rsi_14": feats[6], "above_ma200": feats[7]} if feats else None),
        "recent_events": [{"date": e[0], "event_type": e[1], "materiality": e[2],
                           "headline": (e[3] or "")[:200]} for e in events],
        # Stated rather than absent: there is no sector field in this archive.
        "industry": None, "industry_available": False,
    }


def why_did_it_move(con, isin: str, *, on: date, index_name: str = "Nifty 500") -> dict:
    """Decompose one session's move into market, peer and company-specific.

    Wraps ``attribution/move.explain`` and surfaces its refusals as service errors: a move on a
    circuit band or a residual beyond what a market can produce is not explained, because
    explaining it would attach real filings to a data artifact.
    """
    try:
        r = move.explain(con, isin, on=on, index_name=index_name)
    except move.AttributionError as exc:
        raise ServiceError(NOT_ESTIMABLE, str(exc), isin=isin, on=str(on)) from exc
    c = r["components"]
    return {
        "isin": isin, "date": on, "move": r["move"],
        "explained": r["explainable"],
        "data_quality_flags": r["data_quality_flags"],
        "components": [
            {"part": "market", "value": c["market"],
             "detail": f"beta {r['beta_market']:.2f} x {index_name} "
                       f"{r['index_return']:+.2%}"},
            {"part": "peers", "value": c["peer"],
             "detail": f"gamma {r['beta_peer']:.2f} on an empirical group of "
                       f"{len(r['peers'])} names ranked by trailing correlation"},
            {"part": "drift", "value": c["drift"], "detail": "fitted daily intercept"},
            {"part": "specific", "value": c["specific"],
             "detail": f"{r['specific_in_sigmas']:+.1f} trailing sigma"},
        ],
        "sums_to_move": r["checks_out"],
        "fit": {"sessions": r["fit_sessions"], "ends": r["fit_ends"],
                "residual_sd": r["residual_sd"]},
        "peers": r["peers"][:8],
        "candidate_news": r.get("candidate_news"),
    }


def peer_group(con, isin: str, *, as_of: date, k: int = 12) -> dict:
    """The names this one actually moves with, and a statement of what that is not.

    An empirical peer group. It groups by co-movement, which is what a risk model needs, and it
    cannot distinguish "these are competitors" from "these are both mid-caps" - which is what a
    sector taxonomy would give and this archive does not have.
    """
    try:
        pl = move.peers(con, isin, end=as_of, k=k)
    except move.AttributionError as exc:
        raise ServiceError(NOT_ESTIMABLE, str(exc), isin=isin) from exc
    tickers = dict(con.execute("""
        SELECT isin, MAX(ticker) FROM security_listings
        WHERE isin IN (SELECT UNNEST(?)) GROUP BY isin
    """, [[p["isin"] for p in pl]]).fetchall())
    return {"isin": isin, "as_of": as_of, "basis": "trailing return correlation",
            "not_a_sector_classification": True,
            "peers": [{**p, "ticker": tickers.get(p["isin"])} for p in pl]}
