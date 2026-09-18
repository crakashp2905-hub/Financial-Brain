"""Index name lineage - one continuous series per index across NSE's renames.

NSE renamed its whole index family on 2015-11-09 ("CNX Nifty" -> "Nifty 50", "CNX Bank" ->
"Nifty Bank", ...) and restructured Midcap/Smallcap 100 on 2016-04-01. Keyed on name,
every one of those histories breaks in two - "Nifty 50" would start in November 2015.

How renames are identified, and why this way:

* **Level continuity alone does not work.** ~50 indices were renamed the same day and
  many trade near similar levels; matching "the new index opened where an old one
  closed" paired "CNX Nifty" with "Nifty Auto".
* **A fixed continuity threshold does not work either.** 2015-11-09 was the Monday after
  the Bihar election result; the market gapped down ~2% at the open, so every genuine
  rename showed a ~0.98 open/close ratio.

So a *name rule* proposes each pair (the documented "CNX X" -> "Nifty X" rebrand, plus
explicit exceptions), and the data verifies it: the pair's gap must sit within
``FAMILY_BAND`` of the median gap of all proposed pairs that day. A genuine rename moves
with its family; a wrong pairing shows an arbitrary level difference.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timezone

#: Renames the plain "CNX X" -> "Nifty X" rule cannot express.
EXPLICIT = {
    "CNX Nifty": "Nifty 50", "CNX Nifty Junior": "Nifty Next 50",
    "CNX Midcap": "Nifty Midcap 100", "CNX Smallcap": "Nifty Smallcap 100",
    "CNX Finance": "Nifty Financial Services", "CNX Consumption": "Nifty India Consumption",
    "CNX Service Sector": "Nifty Services Sector", "CNX Shariah25": "Nifty Shariah 25",
    "CPSE": "Nifty CPSE", "CNX Alpha Index": "Nifty Alpha 50",
    "CNX High Beta": "Nifty High Beta 50", "CNX Low Volatility": "Nifty Low Volatility 50",
    "CNX Dividend Opportunities": "Nifty Dividend Opportunities 50",
    "NSE Quality 30": "Nifty Quality 30", "CNX 100 Equal Weight": "Nifty100 Equal Weight",
    "LIX 15": "Nifty100 Liquid 15", "LIX15 Midcap": "Nifty Midcap Liquid 15",
    "NV 20": "Nifty50 Value 20", "NI15": "Nifty Growth Sectors 15",
    "CNX Nifty Shariah": "Nifty50 Shariah", "CNX 500 Shariah": "Nifty500 Shariah",
    "CNX Nifty Dividend": "Nifty50 Dividend Points",
    # 2016-04-01 restructuring of the free-float midcap/smallcap series
    "Nifty Midcap 100": "Nifty Free Float Midcap 100",
    "Nifty Smallcap 100": "Nifty Free Float Smallcap 100",
    # ...and back on 2018-04-02. NSE also published the later name on one stray day
    # (2016-07-07), which is why the new name's first date is taken *after* the old
    # name's last one.
    "Nifty Free Float Midcap 100": "NIFTY Midcap 100",
    "Nifty Free Float Smallcap 100": "NIFTY Smallcap 100",
}
#: A proposed rename's gap must be within this of the family's median gap that day.
FAMILY_BAND = 0.02


def propose(old: str) -> str | None:
    if old in EXPLICIT:
        return EXPLICIT[old]
    if old.startswith("CNX "):
        return "Nifty " + old[4:]
    return None


def detect(con) -> list[dict]:
    spans = {n: (f, l) for n, f, l in con.execute(
        "SELECT index_name, MIN(business_date), MAX(business_date) FROM index_levels "
        "GROUP BY 1").fetchall()}

    def level(name, d, col):
        r = con.execute(f"SELECT {col} FROM index_levels WHERE index_name = ? "
                        "AND business_date = ?", [name, d]).fetchone()
        return r[0] if r else None

    proposals = []
    for old, (_, last) in spans.items():
        new = propose(old)
        if not new or new not in spans:
            continue
        nxt = con.execute("SELECT MIN(business_date) FROM index_levels WHERE index_name = ? "
                          "AND business_date > ?", [new, last]).fetchone()[0]
        if nxt is None:
            continue
        first = nxt
        o = level(new, first, "open_level") or level(new, first, "close_level")
        c = level(old, last, "close_level")
        if o and c:
            proposals.append({"old_name": old, "new_name": new, "effective_date": first,
                              "ratio": o / c})

    by_day: dict = {}
    for p in proposals:
        by_day.setdefault(p["effective_date"], []).append(p["ratio"])
    out = []
    for p in proposals:
        med = statistics.median(by_day[p["effective_date"]])
        p["family_median"] = med
        p["verified"] = abs(p["ratio"] / med - 1) <= FAMILY_BAND
        out.append(p)
    return out


def record(con) -> dict:
    found = detect(con)
    now = datetime.now(timezone.utc)
    con.execute("DELETE FROM index_aliases")
    for p in found:
        if p["verified"]:
            con.execute("INSERT INTO index_aliases VALUES (?,?,?,?,?,?)",
                        [p["old_name"], p["new_name"], p["effective_date"], p["ratio"],
                         p["family_median"], now])
    return {"proposed": len(found), "verified": sum(p["verified"] for p in found),
            "rejected": [p for p in found if not p["verified"]]}


#: Every level under the *current* name of its series (up to four renames deep),
#: one row per series and date.
CANONICAL_VIEW = """
CREATE OR REPLACE VIEW index_levels_canonical AS
SELECT l.business_date,
       COALESCE(a4.new_name, a3.new_name, a2.new_name, a1.new_name, l.index_name) AS index_name,
       l.index_name AS published_name, l.open_level, l.high_level, l.low_level,
       l.close_level, l.variant, l.source
FROM index_levels l
LEFT JOIN index_aliases a1 ON a1.old_name = l.index_name
LEFT JOIN index_aliases a2 ON a2.old_name = a1.new_name
LEFT JOIN index_aliases a3 ON a3.old_name = a2.new_name
LEFT JOIN index_aliases a4 ON a4.old_name = a3.new_name
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY COALESCE(a4.new_name, a3.new_name, a2.new_name, a1.new_name, l.index_name),
                 l.business_date, l.variant
    ORDER BY (l.index_name = COALESCE(a4.new_name, a3.new_name, a2.new_name, a1.new_name, l.index_name))
             DESC) = 1
"""
