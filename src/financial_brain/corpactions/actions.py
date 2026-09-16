"""Corporate actions and price-adjustment history.

Prices are stored **unadjusted** in the curated layer and adjusted on read. That ordering
is deliberate: an adjusted price is a derived opinion that changes every time a new action
lands, whereas the traded price is a fact. Storing the fact and deriving the opinion means
a late-discovered action re-adjusts history correctly instead of corrupting it.

Ratio conventions
-----------------
SPLIT   ``ratio_from`` -> ``ratio_to`` shares (1 -> 5)  price factor = from/to
BONUS   ``ratio_to`` new for every ``ratio_from`` held   price factor = from/(from+to)
RIGHTS  treated as BONUS with an issue price; approximate unless the price is supplied
DIVIDEND price factor = (prev_close - amount) / prev_close, needs a price
Others (MERGER, SYMBOL_CHANGE, DELISTING) carry no price factor; they are identity events.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone

from ..config import TIER

PRICE_AFFECTING = {"SPLIT", "BONUS", "RIGHTS", "DIVIDEND"}


def action_id(isin: str, action_type: str, ex_date: date, detail: str = "") -> str:
    basis = f"{isin}|{action_type}|{ex_date}|{detail}"
    return hashlib.sha256(basis.encode()).hexdigest()[:20]


def record(con, *, isin: str, action_type: str, ex_date: date,
           ratio_from: float | None = None, ratio_to: float | None = None,
           amount: float | None = None, exchange: str | None = None,
           record_date: date | None = None, details: str = "",
           source: str = "MANUAL", published_at: datetime | None = None,
           evidence_key: str | None = None) -> str:
    """Append one corporate action. Idempotent on (isin, type, ex_date, details)."""
    action_type = action_type.upper()
    aid = action_id(isin, action_type, ex_date, details)
    if con.execute("SELECT 1 FROM corporate_actions WHERE action_id = ?", [aid]).fetchone():
        return aid

    con.execute(
        """INSERT INTO corporate_actions
           (action_id, isin, exchange, action_type, ex_date, record_date, ratio_from,
            ratio_to, amount, details, source, source_tier, published_at, observed_at,
            evidence_key)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [aid, isin, exchange, action_type, ex_date, record_date, ratio_from, ratio_to,
         amount, details, source.upper(), TIER.get(source.upper(), 4), published_at,
         datetime.now(timezone.utc), evidence_key],
    )
    return aid


def price_factor(action: dict, prev_close: float | None = None) -> float | None:
    """Multiplier applied to prices *before* the ex-date. ``None`` if not price-affecting."""
    t = (action.get("action_type") or "").upper()
    if t not in PRICE_AFFECTING:
        return None

    f, to = action.get("ratio_from"), action.get("ratio_to")
    if t == "SPLIT" and f and to:
        return float(f) / float(to)
    if t in ("BONUS", "RIGHTS") and f and to:
        return float(f) / (float(f) + float(to))
    if t == "DIVIDEND" and action.get("amount") and prev_close:
        amt, px = float(action["amount"]), float(prev_close)
        return (px - amt) / px if px > amt > 0 else None
    return None


def rebuild_adjustment_factors(con, isin: str | None = None) -> int:
    """Recompute cumulative adjustment factors from the corporate-action history.

    For any date D, the factor is the product of the factors of every price-affecting
    action with ``ex_date > D``. A price observed on D is multiplied by that factor to
    express it in today's terms.
    """
    where = "WHERE action_type IN ('SPLIT','BONUS','RIGHTS','DIVIDEND')"
    params: list = []
    if isin:
        where += " AND isin = ?"
        params.append(isin)

    rows = con.execute(
        f"""SELECT isin, action_type, ex_date, ratio_from, ratio_to, amount, action_id
            FROM corporate_actions {where} ORDER BY isin, ex_date DESC""", params
    ).fetchall()

    if isin:
        con.execute("DELETE FROM adjustment_factors WHERE isin = ?", [isin])
    else:
        con.execute("DELETE FROM adjustment_factors")

    now = datetime.now(timezone.utc)
    written, cumulative, current_isin = 0, 1.0, None

    for r_isin, a_type, ex_date, r_from, r_to, amount, aid in rows:
        if r_isin != current_isin:
            current_isin, cumulative = r_isin, 1.0

        prev_close = None
        if a_type == "DIVIDEND":
            px = con.execute(
                """SELECT close_price FROM universe_snapshots
                   WHERE isin = ? AND business_date < ? AND close_price IS NOT NULL
                   ORDER BY business_date DESC LIMIT 1""", [r_isin, ex_date]
            ).fetchone()
            prev_close = px[0] if px else None

        f = price_factor({"action_type": a_type, "ratio_from": r_from,
                          "ratio_to": r_to, "amount": amount}, prev_close)
        if f is None or f <= 0:
            continue

        cumulative *= f
        con.execute(
            """INSERT INTO adjustment_factors
               (isin, effective_from, price_factor, volume_factor, derived_from, computed_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT (isin, effective_from) DO UPDATE SET
                   price_factor = EXCLUDED.price_factor,
                   volume_factor = EXCLUDED.volume_factor,
                   computed_at = EXCLUDED.computed_at""",
            [r_isin, ex_date, cumulative, 1.0 / cumulative if cumulative else 1.0, aid, now],
        )
        written += 1
    return written


def adjusted_prices(con, isin: str, start: date | None = None, end: date | None = None) -> list[dict]:
    """Close prices expressed in today's terms, alongside the unadjusted fact."""
    sql = """
        SELECT p.business_date, p.close_price AS close_unadjusted,
               p.close_price * COALESCE((
                   SELECT MIN(a.price_factor) FROM adjustment_factors a
                   WHERE a.isin = p.isin AND a.effective_from > p.business_date
               ), 1.0) AS close_adjusted,
               COALESCE((
                   SELECT MIN(a.price_factor) FROM adjustment_factors a
                   WHERE a.isin = p.isin AND a.effective_from > p.business_date
               ), 1.0) AS factor
        FROM universe_snapshots p
        WHERE p.isin = ? AND p.close_price IS NOT NULL
    """
    params: list = [isin]
    if start:
        sql += " AND p.business_date >= ?"
        params.append(start)
    if end:
        sql += " AND p.business_date <= ?"
        params.append(end)
    sql += " ORDER BY p.business_date"

    cur = con.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def detect_suspicious_gaps(con, threshold: float = 0.20, min_turnover: float = 1_000_000) -> list[dict]:
    """Overnight moves large enough to suggest an unrecorded corporate action.

    A 1:5 split shows up as a ~80% overnight 'fall' that never happened. Until the action
    history is complete this is the cheapest way to find the holes - and it is itself a
    data-quality contract worth running after every ingest.
    """
    cur = con.execute("""
        WITH d AS (
            SELECT isin, ticker, business_date, close_price, turnover,
                   LAG(close_price) OVER (PARTITION BY isin ORDER BY business_date) AS prev_close,
                   LAG(business_date) OVER (PARTITION BY isin ORDER BY business_date) AS prev_date
            FROM universe_snapshots
            WHERE instrument_type = 'STK' AND close_price > 0
        )
        SELECT isin, ticker, prev_date, business_date, prev_close, close_price,
               (close_price / prev_close - 1) AS pct_change
        FROM d
        WHERE prev_close IS NOT NULL
          AND turnover >= ?
          AND ABS(close_price / prev_close - 1) >= ?
          AND NOT EXISTS (
              SELECT 1 FROM corporate_actions ca
              WHERE ca.isin = d.isin AND ca.ex_date > d.prev_date AND ca.ex_date <= d.business_date
          )
        ORDER BY ABS(close_price / prev_close - 1) DESC
    """, [min_turnover, threshold])
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]
