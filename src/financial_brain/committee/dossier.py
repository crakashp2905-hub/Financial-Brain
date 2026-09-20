"""The committee's dossier on one company: Tier-0 facts only, each a cited claim.

Nothing in here is produced by a model. Price features, the market regime, recent
filings (with any model-read tone *labelled as such*), the promoter group and pledge
activity, and the constitution's verdict on a hypothetical BUY - each becomes an
evidence-ledger claim with an id. Analysts and debaters may cite only these ids; the
committee cannot introduce a fact the dossier does not hold.

Point in time: everything is as of the world state's session and published before its
as-of time.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, time

from ..config import TIER
from ..evidence import ledger

CLOSE = time(15, 30)
FEATURES = [("ret_20d", "20-session return", "{:+.1%}"), ("ret_60d", "60-session return",
            "{:+.1%}"), ("mom_12_1", "12-1 momentum", "{:+.1%}"),
            ("vol_60", "60-session annualised volatility", "{:.0%}"),
            ("dist_52w_high", "distance from 52-week high", "{:+.1%}"),
            ("adv20", "20-session average traded value", "Rs {:,.0f}")]


@dataclass
class Fact:
    id: str                    # evidence_id
    kind: str                  # price | market | filing | governance | constitution
    text: str


@dataclass
class Dossier:
    isin: str
    company: str
    world_state_version: str
    as_of: datetime
    facts: list[Fact] = field(default_factory=list)

    def ids(self) -> set[str]:
        return {f.id for f in self.facts}

    def render(self, kinds: set[str] | None = None) -> str:
        return "\n".join(f"[{f.id}] ({f.kind}) {f.text}" for f in self.facts
                         if kinds is None or f.kind in kinds)


def build(con, isin: str, world_state_version: str, *, filings_days: int = 90,
          constitution: dict | None = None) -> Dossier:
    d, content = con.execute("""SELECT business_date, content FROM world_states
                                WHERE version_id = ?""", [world_state_version]).fetchone()
    ws = json.loads(content)
    as_of = datetime.fromisoformat(str(ws["as_of"]))
    close_ts = datetime.combine(d, CLOSE)
    name = con.execute("""SELECT company FROM announcements WHERE isin = ?
                          ORDER BY business_date DESC LIMIT 1""", [isin]).fetchone()
    doss = Dossier(isin, name[0] if name else isin, world_state_version, as_of)

    def add(kind, text, value, source, derivation, published=None, lake_key=None,
            inputs=None, at=None):
        eid = ledger.mint(con, kind=f"dossier_{kind}", subject=isin, as_of=at or close_ts,
                          claim=text, value=value, source=source, source_tier=TIER[source],
                          derivation=derivation, published_at=published, lake_key=lake_key,
                          inputs=inputs)
        doss.facts.append(Fact(eid, kind, text))

    # -- price features (features f1 over adjusted prices), as of the session --------
    row = con.execute(f"""SELECT f.business_date, {', '.join('f.' + c for c, _, _ in FEATURES)}
        FROM features f JOIN security_lineage l ON l.lineage = f.lineage
        WHERE l.isin = ? AND f.business_date <= ? ORDER BY f.business_date DESC LIMIT 1""",
                      [isin, d]).fetchone() if _has(con, "features") else None
    if row:
        for (col, label, fmt), v in zip(FEATURES, row[1:]):
            if v is not None:
                add("price", f"{label}: {fmt.format(v)} (as of {row[0]})", {col: v}, "NSE",
                    "features f1 over adjusted NSE closes")

    # -- market regime from the world state ------------------------------------------
    m = ws.get("market") or {}
    if m.get("regime"):
        add("market", f"market regime {m['regime']}: {m.get('reasons', '')}",
            {"regime": m["regime"]}, "NSE", "regime brain v2 (world state)")

    # -- filings in the window, with model tone labelled as a model's reading --------
    for nid, bd, kind, mat, head, subj, pub, key, tone, model, ok in con.execute("""
            SELECT a.news_id, a.business_date, a.event_type, a.materiality, a.headline,
                   a.subject, a.published_at, a.evidence_key, t.tone, t.model, t.accepted
            FROM announcements a LEFT JOIN announcement_tone t USING (news_id)
            WHERE a.isin = ? AND a.materiality IN ('high', 'medium')
              AND a.published_at <= ? AND a.business_date > ? - ?::INTEGER
            ORDER BY a.published_at DESC LIMIT 12""",
            [isin, as_of, d, filings_days]).fetchall():
        text = head if head and len(head) >= 25 else (subj or head or "")
        note = f" [model {model} reads it {tone}]" if ok and tone and tone != "neutral" else ""
        add("filing", f"{bd} {kind} ({mat}): {text[:220]}{note}",
            {"news_id": nid, "event_type": kind}, "BSE", "BSE announcements, events/classify",
            published=pub, lake_key=key, at=pub)

    # -- governance: promoter group and pledges (knowledge graph) ---------------------
    if _has(con, "holder_filings"):
        n = con.execute("""SELECT COUNT(*) FROM holder_filings WHERE isin = ? AND
                           relation = 'PLEDGE' AND business_date > ? - 365
                           AND business_date <= ?""", [isin, d, d]).fetchone()[0]
        add("governance", f"{n} promoter pledge/encumbrance filings in the last 365 days",
            {"pledge_filings_365d": n}, "BSE", "graph: Reg. 31 filings")
        from ..graph import build as graph
        g = next((g for g in graph.groups(con, as_of=d) if isin in g["isins"]), None)
        if g:
            others = [c for m_, c in zip(g["members"], g["companies"])
                      if isin not in g["member_isins"][m_]]
            add("governance", f"member of the {g['anchor'] or 'unnamed'} promoter group; "
                f"other listed members: {', '.join(others[:8])}",
                {"group_id": g["group_id"]}, "BSE", "graph: corroborated promoter links")

    # -- valuation ratios compiled by Screener (Tier 3, cross-checked) ---------------
    #    Stored snapshots only: a dossier reads what we already hold, it does not reach
    #    out to a website while a committee is sitting. The claim keeps its tier, so a
    #    debater can see this is a compiler's number, not the company's filing.
    if _has(con, "company_fundamentals"):
        val = valuation_as_of(con, isin, d)
        if val:
            add("valuation", val["text"], val["value"], "SCREENER",
                "providers/screener top-ratios", lake_key=val["lake_key"])

    # -- what the constitution would say to a BUY now --------------------------------
    from ..config import load
    from ..constitution import rules
    book = constitution if constitution is not None else rules.load(load().data_root)
    broken = rules.check(con, {"isin": isin, "action": "BUY", "horizon_days": 90,
                               "world_state_version": world_state_version,
                               "sizing": {}}, book)
    add("constitution", ("a BUY would pass the constitution" if not broken else
                         "a BUY would break: " + "; ".join(f"{b['rule']} ({b['fact']})"
                                                          for b in broken))
        + (" [example constitution - owner's rules not yet written]"
           if book.get("_is_example") else ""),
        {"violations": [b["rule"] for b in broken]}, "DERIVED",
        "constitution/rules.py")
    return doss


def _has(con, name: str) -> bool:
    return bool(con.execute("""SELECT 1 FROM information_schema.tables
                               WHERE table_name = ?""", [name]).fetchone())


RATIOS_WANTED = ("Stock P/E", "Book Value", "ROCE", "ROE", "Dividend Yield", "Market Cap")


def valuation_as_of(con, isin: str, d) -> dict | None:
    """The most recent Screener snapshot for this ISIN that existed **on or before** ``d``.

    Point-in-time matters as much for a Tier-3 ratio as for a price: a snapshot taken
    today must not appear in a dossier reconstructing what was knowable last week, or a
    replayed decision silently improves on the one that was actually made.
    """
    row = con.execute("""SELECT f.symbol, f.ratios, f.price_check, f.fetched_on, f.lake_key
                         FROM company_fundamentals f
                         JOIN (SELECT DISTINCT ticker, isin FROM eod_prices) p
                           ON p.ticker = f.symbol
                         WHERE p.isin = ? AND f.fetched_on <= ?
                         ORDER BY f.fetched_on DESC LIMIT 1""", [isin, d]).fetchone()
    if not row:
        return None
    symbol, ratios_json, check_json, on, key = row
    ratios = json.loads(ratios_json)
    check = json.loads(check_json) if check_json else None
    wanted = [k for k in RATIOS_WANTED if k in ratios]
    if not wanted:
        return None
    text = ", ".join(f"{k} {ratios[k]['raw']}" for k in wanted)
    if check and not check["agrees"]:
        text += (f" - disputed: Screener's price {check['screener_price']} is "
                 f"{check['drift']:.1%} from our close {check['our_close']}")
    return {"text": f"{text} (Screener, {on})", "lake_key": key,
            "value": {"symbol": symbol, "ratios": {k: ratios[k] for k in wanted},
                      "price_check": check}}
