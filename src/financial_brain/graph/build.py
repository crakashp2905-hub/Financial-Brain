"""Promoter-group knowledge graph (C24), rebuilt from the announcements table.

Facts: ``holder_filings`` - one row per SAST / insider disclosure whose headline names a
filer (``extract.filer``), with the regulation's meaning (``extract.relation``). Every
row points back to its announcement, so every edge in the graph is traceable to the
filing that created it.

Groups: two listed companies are in one promoter group when their *promoter-evidence*
filers (Reg. 31 pledges, Reg. 10 exempt inter-se transfers; never institutions) overlap
and the overlap is corroborated:

* at least two distinct filers are shared, or
* one shared organisation filed at least twice in each company.

Single-filing and single-person links are not enough. On 2015-2026 data that rule is
what separates real groups (Tata Sons, Adani family trusts, Siddeshwari Tradex across
JSW/Jindal) from coincidences (a common person's name across unrelated companies, a
pledgee's one-off filing joining Sun Pharma to Kesoram). A filer spanning more than
``MAX_SPAN`` companies is treated as too broad to be a promoter and never bridges.

Known limit, stated: the Birla family's companies are genuinely cross-held (Pilani
Investment), so Aditya Birla and K.K./B.K. Birla companies come out as one group.

Point in time: ``build(as_of=D)`` uses only filings published on or before D.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter, defaultdict
from datetime import date

from . import extract

MAX_SPAN = 15
MIN_ORG_FILINGS = 2

_SELECT = """
    SELECT news_id, business_date, published_at, scrip_code, isin, company, subcategory,
           headline
    FROM announcements
    WHERE regexp_matches(subcategory, '(?i)sast|insider|\\(pit\\)|reg\\.? ?31|encumbr|pledge')
"""


def extract_filings(con) -> dict:
    """(Re)build holder_filings from announcements. Idempotent."""
    out, seen, named = [], 0, 0
    for nid, bd, pub, scrip, isin, company, sub, head in con.execute(_SELECT).fetchall():
        seen += 1
        rel = extract.relation(sub or "", head or "")
        name = extract.filer(head or "")
        if not rel or not name:
            continue
        k = extract.key(name)
        if not extract.usable(k):
            continue
        named += 1
        out.append({"news_id": nid, "business_date": str(bd),
                    "published_at": pub.isoformat() if pub else None,
                    "scrip_code": scrip, "isin": isin, "company": company,
                    "relation": rel, "filer": name, "filer_key": k,
                    "filer_kind": extract.kind(name)})
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for r in out:
            fh.write(json.dumps(r) + "\n")
    try:
        con.execute("DELETE FROM holder_filings")
        if out:
            con.execute(f"""INSERT INTO holder_filings SELECT news_id, CAST(business_date AS DATE),
                CAST(published_at AS TIMESTAMP), scrip_code, isin, company, relation, filer,
                filer_key, filer_kind FROM read_json('{path.replace(os.sep, '/')}',
                format='newline_delimited', columns={{news_id:'VARCHAR', business_date:'VARCHAR',
                published_at:'VARCHAR', scrip_code:'VARCHAR', isin:'VARCHAR', company:'VARCHAR',
                relation:'VARCHAR', filer:'VARCHAR', filer_key:'VARCHAR',
                filer_kind:'VARCHAR'}})""")
    finally:
        os.remove(path)
    return {"disclosures": seen, "with_filer": named}


def groups(con, as_of: date | None = None) -> list[dict]:
    """Promoter groups as known on ``as_of`` (all history if None)."""
    rows = con.execute("""
        SELECT isin, scrip_code, company, filer_key, filer, filer_kind, COUNT(*),
               MIN(business_date), MAX(business_date)
        FROM holder_filings
        WHERE relation IN ('PLEDGE', 'EXEMPT') AND filer_kind <> 'institution'
          AND (CAST(? AS DATE) IS NULL OR business_date <= CAST(? AS DATE))
        GROUP BY ALL""", [as_of, as_of]).fetchall()
    # One company is one member across ISIN changes (splits, face-value changes, rows
    # filed before the ISIN resolved): key by BSE scrip code, which survives all of them,
    # and name each member by its most recent ISIN.
    by_filer: dict[str, set] = defaultdict(set)
    n: Counter = Counter()
    kinds, label, isins = {}, {}, defaultdict(set)
    latest: dict[str, date] = {}
    latest_isin: dict[str, tuple] = {}
    spelling: Counter = Counter()
    for isin, scrip, company, k, name, kind, cnt, _, last in rows:
        c = f"BSE:{scrip}" if scrip else isin
        if isin and (c not in latest_isin or last > latest_isin[c][0]):
            latest_isin[c] = (last, isin)
        by_filer[k].add(c)
        n[(k, c)] += cnt
        kinds[k] = kind
        spelling[(k, name)] += cnt
        if isin:
            isins[c].add(isin)
        if c not in latest or last > latest[c]:                # the most recent name
            latest[c], label[c] = last, company
    names = {}                                   # each filer shown as most often spelt
    for (k, name), _count in sorted(spelling.items(), key=lambda kv: kv[1]):
        names[k] = name
    shared: dict[tuple, set] = defaultdict(set)
    for k, cs in by_filer.items():
        if len(cs) > MAX_SPAN:
            continue
        cs = sorted(cs)
        for i, a in enumerate(cs):
            for b in cs[i + 1:]:
                shared[(a, b)].add(k)
    parent: dict[str, str] = {}

    def find(a):
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    links = []
    for (a, b), ks in shared.items():
        strong = [k for k in ks if kinds[k] == "organisation"
                  and n[(k, a)] >= MIN_ORG_FILINGS and n[(k, b)] >= MIN_ORG_FILINGS]
        if len(ks) >= 2 or strong:
            parent[find(a)] = find(b)
            links.append((a, b, sorted(ks)))
    members: dict[str, list] = defaultdict(list)
    for c in list(parent):
        members[find(c)].append(c)
    rename = {c: latest_isin[c][1] if c in latest_isin else c for c in parent}
    n = Counter({(k, rename.get(c, c)): v for (k, c), v in n.items()})
    label = {rename.get(c, c): v for c, v in label.items()}
    isins = {rename.get(c, c): v for c, v in isins.items()}
    links = [(rename.get(a, a), rename.get(b, b), ks) for a, b, ks in links]
    members = {r: [rename[c] for c in cs] for r, cs in members.items()}
    out = []
    for cs in members.values():
        weight = Counter()
        for (k, c), cnt in n.items():
            if c in cs and kinds[k] == "organisation":
                weight[k] += cnt
        anchor = weight.most_common(1)[0][0] if weight else ""
        cs = sorted(cs)
        out.append({"group_id": "grp_" + hashlib.sha256("|".join(cs).encode()).hexdigest()[:12],
                    "anchor": names.get(anchor, ""), "members": cs,
                    "companies": [label[c] for c in cs],
                    "isins": sorted(i for c in cs for i in isins.get(c, set()) | {c}
                                    if not i.startswith("BSE:")),
                    "member_isins": {c: sorted(isins.get(c, set()) | {c}) for c in cs},
                    "links": [lk for lk in links if lk[0] in cs]})
    return sorted(out, key=lambda g: -len(g["members"]))


def build(con, as_of: date | None = None) -> dict:
    stats = extract_filings(con)
    gs = groups(con, as_of)
    con.execute("DELETE FROM promoter_groups")
    for g in gs:
        for c, name in zip(g["members"], g["companies"]):
            con.execute("INSERT INTO promoter_groups VALUES (?, ?, ?, ?, ?)",
                        [g["group_id"], g["anchor"], c, name, len(g["members"])])
    stats.update(groups=len(gs), grouped=sum(len(g["members"]) for g in gs),
                 largest=len(gs[0]["members"]) if gs else 0)
    return stats


def profile(con, isin: str, as_of: date | None = None, days: int = 365) -> dict:
    """Everything the graph knows about one company: its promoter filers, its group,
    and recent pledge activity - each with the filings behind it."""
    q = """SELECT relation, filer, filer_kind, COUNT(*), MAX(business_date),
                  LIST(news_id ORDER BY business_date DESC)[1:3]
           FROM holder_filings WHERE isin = ?
             AND (CAST(? AS DATE) IS NULL OR business_date <= CAST(? AS DATE))
           GROUP BY ALL ORDER BY 4 DESC"""
    filers = con.execute(q, [isin, as_of, as_of]).fetchall()
    recent = con.execute("""SELECT COUNT(*), COUNT(DISTINCT filer_key) FROM holder_filings
        WHERE isin = ? AND relation = 'PLEDGE'
          AND business_date > COALESCE(CAST(? AS DATE), CURRENT_DATE) - ?
          AND (CAST(? AS DATE) IS NULL OR business_date <= CAST(? AS DATE))""",
                         [isin, as_of, days, as_of, as_of]).fetchone()
    group = next((g for g in groups(con, as_of) if isin in g["isins"]), None)
    return {"isin": isin,
            "filers": [dict(zip(["relation", "filer", "kind", "filings", "last", "news_ids"],
                                r)) for r in filers],
            "pledge_filings_recent": recent[0], "pledge_filers_recent": recent[1],
            "recent_days": days, "group": group}
