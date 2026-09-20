"""ISIN successions: the same security continuing under a new ISIN.

In India a face-value split changes the ISIN. Keyed on ISIN alone, every split breaks a
company's history into two unrelated securities - and the split itself becomes an
"unexplained gap" that cannot be corroborated, because on the ex-date one exchange
already shows the new ISIN while the other still shows the old one. Over 2015-2026 that
accounted for most of the gaps left as ``needs_source`` (GREENLAM 2022-02-10,
AMRUTANJAN 2018-04-13, ...).

What survives the ISIN change is the exchange's own handle on the security:

* **BSE scrip code** (``instrument_id``) - 884 codes carry more than one ISIN;
* **NSE ticker**.

A succession is recorded when, on one exchange, the handle's old ISIN last traded on the
session immediately before the new ISIN first traded. The pair is **corroborated** when
both exchanges show the same ``(old, new)`` pair - measured at 194 of 696 BSE
successions. Consecutive sessions are required so a ticker later reused for an unrelated
company cannot link the two.

The price ratio across the switch is kept: ~1 means an identity-only change (scheme
restructure, demat re-issue); a clean fall is the split factor.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timezone

_PAIRS_SQL = """
    WITH cal AS (
        SELECT exchange, business_date,
               LEAD(business_date) OVER (PARTITION BY exchange ORDER BY business_date) AS nxt
        FROM (SELECT DISTINCT exchange, business_date FROM universe_snapshots)
    ), spans AS (
        SELECT exchange,
               CASE WHEN exchange = 'BSE' THEN instrument_id ELSE ticker END AS handle,
               isin, MIN(first_seen) AS f, MAX(last_seen) AS l
        FROM security_listings
        WHERE CASE WHEN exchange = 'BSE' THEN instrument_id ELSE ticker END <> ''
          -- Equity shares (ISIN security type '01') and fund units (INF...) only. A
          -- debenture's ISIN also changes on partial redemption (INE721A07ON4 showed a
          -- 0.75 "split"), and that is not an adjustment to anything we price.
          AND (isin LIKE 'INF%' OR substr(isin, 8, 2) = '01')
        GROUP BY 1, 2, 3
    ), pairs AS (
        SELECT a.exchange, a.handle, a.isin AS old_isin, b.isin AS new_isin,
               a.l AS old_last, b.f AS new_first
        FROM spans a
        JOIN spans b ON b.exchange = a.exchange AND b.handle = a.handle AND b.isin <> a.isin
        JOIN cal c   ON c.exchange = a.exchange AND c.business_date = a.l AND c.nxt = b.f
    )
    SELECT exchange, handle, old_isin, new_isin, old_last, new_first FROM pairs
"""

#: The price change across the ex-date, per exchange: the first price on the ex-date
#: under either ISIN of the pair (the open where known, else the close), over the old
#: ISIN's close on the exchange's previous session.
#:
#: Measured this way because the exchanges do not switch ISINs on the same day. On
#: Schaeffler's 1:5 split (2022-02-08) BSE showed the new ISIN while NSE kept the *old*
#: ISIN one more session at the already-split price - so "old last close -> new first
#: close" gave 0.197 on BSE but 0.96 on NSE, and their median (0.58) snapped to a
#: meaningless "3:5". Across the ex-date both exchanges give ~0.197.
_RATIO_SQL = """
    WITH cal AS (
        SELECT exchange, business_date,
               LAG(business_date) OVER (PARTITION BY exchange ORDER BY business_date) AS prev
        FROM (SELECT DISTINCT exchange, business_date FROM universe_snapshots)
    ), c AS (
        SELECT x.*, cal.prev FROM _succ_cand x
        JOIN cal ON cal.exchange = x.exchange AND cal.business_date = x.eff
    ), px_open AS (
        SELECT e.exchange, e.business_date, e.isin, MAX(e.open_price) AS o
        FROM eod_prices e JOIN (SELECT DISTINCT exchange, eff FROM c) k
          ON k.exchange = e.exchange AND k.eff = e.business_date
        WHERE e.open_price > 0 GROUP BY 1, 2, 3
    )
    SELECT c.exchange, c.old_isin, c.new_isin,
           COALESCE(
             (SELECT MAX(o) FROM px_open p WHERE p.exchange = c.exchange
                AND p.business_date = c.eff AND p.isin IN (c.old_isin, c.new_isin)),
             (SELECT MAX(close_price) FROM universe_snapshots u WHERE u.exchange = c.exchange
                AND u.business_date = c.eff AND u.isin IN (c.old_isin, c.new_isin)))
           / NULLIF((SELECT MAX(close_price) FROM universe_snapshots u
                      WHERE u.exchange = c.exchange AND u.business_date = c.prev
                        AND u.isin = c.old_isin), 0) AS ratio
    FROM c
"""


def detect(con) -> list[dict]:
    """All successions, one per (old, new) pair, graded by cross-exchange agreement."""
    by_pair: dict[tuple, list] = {}
    for exch, handle, old, new, old_last, new_first in con.execute(_PAIRS_SQL).fetchall():
        by_pair.setdefault((old, new), []).append((exch, handle, old_last, new_first))

    # The ex-date is the first switch on either exchange; measure every exchange there.
    eff = {k: min(o[3] for o in obs) for k, obs in by_pair.items()}
    con.execute("CREATE OR REPLACE TEMP TABLE _succ_cand "
                "(exchange VARCHAR, old_isin VARCHAR, new_isin VARCHAR, eff DATE)")
    cand = [[o[0], old, new, eff[(old, new)]]
            for (old, new), obs in by_pair.items() for o in obs]
    if cand:
        con.executemany("INSERT INTO _succ_cand VALUES (?,?,?,?)", cand)
    ratios: dict[tuple, list] = {}
    for _exch, old, new, r in con.execute(_RATIO_SQL).fetchall():
        if r:
            ratios.setdefault((old, new), []).append(r)

    out = []
    for (old, new), obs in by_pair.items():
        exchanges = sorted({o[0] for o in obs})
        rs = ratios.get((old, new), [])
        out.append({
            "old_isin": old, "new_isin": new,
            "effective_date": eff[(old, new)],
            "exchanges": "+".join(exchanges),
            "price_ratio": statistics.median(rs) if rs else None,
            "confidence": "corroborated" if len(exchanges) > 1 else "single_exchange",
            "evidence": "; ".join(f"{o[0]} {'scrip' if o[0] == 'BSE' else 'ticker'} {o[1]}: "
                                  f"old last {o[2]}, new first {o[3]}" for o in obs),
        })
    return sorted(out, key=lambda r: (r["effective_date"], r["old_isin"]))


def record(con) -> dict:
    """Replace the succession table with a fresh detection. Returns counts."""
    found = detect(con)
    now = datetime.now(timezone.utc)
    con.execute("DELETE FROM isin_successions")
    for s in found:
        con.execute(
            """INSERT INTO isin_successions (old_isin, new_isin, effective_date, exchanges,
               price_ratio, confidence, evidence, observed_at) VALUES (?,?,?,?,?,?,?,?)""",
            [s["old_isin"], s["new_isin"], s["effective_date"], s["exchanges"],
             s["price_ratio"], s["confidence"], s["evidence"], now])
    return {"successions": len(found),
            "corroborated": sum(s["confidence"] == "corroborated" for s in found)}
