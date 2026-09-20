"""Daily brief (C10) - "what changed since yesterday?", every line cited.

Rendered deterministically from one world-state version: no model, no free text that
the data does not support. Every factual line carries a numbered citation, and the
*Sources* section resolves each number to its evidence_id, the source file's URL and
SHA-256 - so any sentence can be traced to the exact bytes it came from.

Ordering is the editorial judgement, made explicit:
* red flags before good news (insolvency, auditor resignation, promoter pledge, legal and
  tax orders), then results, ratings, orders, deals, fund raising, management changes;
* within a type, by the company's traded value that session - a large company's news
  outranks a micro-cap's;
* a red flag in a company that belongs to a promoter group names the group and its
  other listed companies (P2-4) - group contagion is India's distinctive risk;
* the reader's own watchlist (``data/watchlist.txt``: one ticker or ISIN per line,
  personal, never committed) before everything else.

The prose layer (an LLM summarising, never predicting) comes later and must cite the
same evidence. This deterministic brief is what it has to improve on.
"""
from __future__ import annotations

from datetime import date

from ..evidence import ledger

EVENT_ORDER = ["INSOLVENCY", "AUDITOR_RESIGNATION", "PROMOTER_PLEDGE", "LEGAL_REGULATORY",
               "CLARIFICATION", "RESULTS", "CREDIT_RATING", "ORDER_WIN", "ACQUISITION",
               "SCHEME", "JOINT_VENTURE", "FUND_RAISING", "MANAGEMENT_CHANGE",
               "CORPORATE_ACTION"]
LABEL = {"INSOLVENCY": "Insolvency", "AUDITOR_RESIGNATION": "Auditor resignations",
         "PROMOTER_PLEDGE": "Promoter pledges / encumbrances",
         "LEGAL_REGULATORY": "Legal, tax and regulatory orders",
         "CLARIFICATION": "Clarifications on price movement", "RESULTS": "Results",
         "CREDIT_RATING": "Credit ratings", "ORDER_WIN": "Order wins",
         "ACQUISITION": "Acquisitions", "SCHEME": "Schemes and restructuring",
         "JOINT_VENTURE": "Joint ventures", "FUND_RAISING": "Fund raising",
         "MANAGEMENT_CHANGE": "Key management changes",
         "CORPORATE_ACTION": "Corporate actions"}
PER_TYPE = 6


class _Cite:
    def __init__(self):
        self.ids: list[str] = []

    def __call__(self, eid: str | None) -> str:
        if not eid:
            return ""
        if eid not in self.ids:
            self.ids.append(eid)
        return f"[{self.ids.index(eid) + 1}]"


def _ranges(nums: list[int]) -> str:
    """[1, 2, 3, 7] -> '[1–3] [7]'."""
    runs, start = [], nums[0]
    for a, b in zip(nums, nums[1:] + [None]):
        if b != a + 1:
            runs.append(f"[{start}]" if start == a else f"[{start}–{a}]")
            start = b
    return " ".join(runs)


def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:+.2f}%"


DOC_LABEL = {"order_value": "order value", "tax_demand": "tax demand",
             "penalty": "penalty", "default": "amount in default",
             "acquisition": "deal value", "fund_raise": "raising",
             "dividend_per_share": "dividend", "demand_set_aside": "demand set aside"}


def _doc_fact(f: dict) -> str:
    """A number read out of the filing's own document."""
    label = DOC_LABEL.get(f["kind"], f["kind"].replace("_", " "))
    if f["unit"] == "INR_per_share":
        return f"**{label} ₹{f['value']:,.2f} per share**"
    if f["unit"] == "INR":
        v = f["value"]
        amount = (f"₹{v / 10 ** 7:,.2f} crore" if v >= 10 ** 7 else
                  f"₹{v / 10 ** 5:,.2f} lakh" if v >= 10 ** 5 else f"₹{v:,.0f}")
    else:
        amount = f"{f['unit']} {f['value']:,.0f}"
    return f"**{label} {amount}** (document)"


def _watchlist(path) -> set[str]:
    if not path or not path.exists():
        return set()
    return {ln.strip().upper() for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")}


def render(con, state: dict, *, watchlist_path=None) -> tuple[str, list[str]]:
    """Return ``(markdown, cited evidence_ids)``."""
    cite, out = _Cite(), []
    d = state["business_date"]
    m = state.get("market")
    watch = _watchlist(watchlist_path)

    liq = dict(con.execute("""SELECT isin, MAX(turnover) FROM universe_snapshots
                              WHERE business_date = ? GROUP BY isin""", [d]).fetchall())
    tickers = dict(con.execute("""SELECT isin, ANY_VALUE(ticker) FROM universe_snapshots
                                  WHERE business_date = ? AND exchange = 'NSE'
                                  GROUP BY isin""", [d]).fetchall())

    out.append(f"# Market brief — {date.fromisoformat(str(state['as_of'])[:10]):%A %d %b %Y}")
    out.append(f"_Session covered: {d}. Events published up to {state['as_of']} IST. "
               f"World state `{state['version_id']}`._\n")

    # ---- regime ---------------------------------------------------------------
    if m:
        out.append(f"## Market: **{m['regime'].replace('_', ' ')}** {cite(m['evidence'])}")
        out.append(f"{m['reasons']}.")
        if m.get("advances") is not None:
            b = f", {m['breadth_200']:.0%} above their 200-session average" \
                if m.get("breadth_200") is not None else ""
            out.append(f"Breadth: {m['advances']:,} advances, {m['declines']:,} declines{b} "
                       f"{cite(m['breadth_evidence'])}.")
        out.append("")

    # ---- watchlist --------------------------------------------------------------
    if watch:
        hits = [e for e in state["events"] if (e.get("isin") or "").upper() in watch
                or (tickers.get(e.get("isin")) or "").upper() in watch]
        moves = [x for x in state["gainers"] + state["losers"]
                 if x["isin"].upper() in watch or x["ticker"].upper() in watch]
        out.append("## Your watchlist")
        if not hits and not moves:
            out.append("Nothing material on your watchlist in this window.")
        for x in moves:
            out.append(f"- **{x['ticker']}** {_pct(x['pct'])} at {x['close']:,.2f} "
                       f"{cite(x['evidence'])}")
        for e in hits:
            out.append(f"- **{tickers.get(e['isin']) or e['company']}** — "
                       f"{LABEL.get(e['event_type'], e['event_type'])}: {e['text'][:200]} "
                       f"{cite(e['evidence'])}")
        out.append("")

    # ---- live theses and whether anything has broken them ---------------------------
    theses = state.get("theses") or []
    if theses:
        out.append("## Theses under watch")
        for t in theses:
            name = tickers.get(t["isin"]) or t["isin"]
            out.append(f"- **{t['action']} {name}** ({t['state'].replace('_', ' ').lower()}) "
                       f"— {t['thesis']}")
            for a in t["alerts"]:
                out.append(f"  - **invalidated** ({a['check']}): {a['detail'][:150]} "
                           f"{cite(a['evidence'])}")
            if not t["alerts"]:
                out.append("  - no invalidation condition has fired")
            if t["unmonitored"]:
                out.append(f"  - {t['unmonitored']} stated condition(s) have no automatic "
                           f"check: still on you to watch")
        out.append("")

    # ---- indices and sectors -------------------------------------------------------
    out.append("## Indices")
    out.append("| Index | Close | Day | 20 sessions |")
    out.append("|---|---:|---:|---:|")
    for i in state["indices"]:
        out.append(f"| {i['name']} {cite(i['evidence'])} | {i['close']:,.2f} | "
                   f"{_pct(i['chg_1d'])} | {_pct(i['chg_20d'])} |")
    if state["sectors"]:
        best, worst = state["sectors"][:3], state["sectors"][-3:][::-1]
        out.append("")
        out.append("**Sectors** — leading: " + ", ".join(
            f"{s['name'].replace('Nifty ', '')} {_pct(s['chg_1d'])} {cite(s['evidence'])}"
            for s in best) + "; lagging: " + ", ".join(
            f"{s['name'].replace('Nifty ', '')} {_pct(s['chg_1d'])} {cite(s['evidence'])}"
            for s in worst) + ".")
    out.append("")

    # ---- movers -------------------------------------------------------------------
    if state["gainers"] or state["losers"]:
        out.append("## Biggest moves (NSE, traded value ≥ ₹10 cr)")
        for rows in (state["gainers"][:5], state["losers"][:5]):
            for x in rows:
                why = "; ".join(f"{LABEL.get(f['event_type'], f['event_type'].replace('_', ' ').capitalize())}: "
                                f"{f['text'][:110]} {cite(f['evidence'])}"
                                for f in x.get("filings", [])[:2])
                out.append(f"- **{x['ticker']}** {_pct(x['pct'])} {cite(x['evidence'])}"
                           + (f" — filed: {why}" if why else " — no filing in the window"))
        out.append("")

    # ---- events ---------------------------------------------------------------------
    events = state["events"]
    out.append(f"## What was announced ({len(events)} high-materiality filings)")
    by_type: dict[str, list] = {}
    for e in events:
        by_type.setdefault(e["event_type"], []).append(e)
    shown = 0
    for kind in EVENT_ORDER + sorted(set(by_type) - set(EVENT_ORDER)):
        # Within a type: a filing a model reads as adverse comes first, then by the
        # company's traded value. Reordering only - the tone itself is shown as that
        # model's reading, cited, never as a fact.
        rows = sorted(by_type.get(kind, []),
                      key=lambda e: (0 if (e.get("model_tone") or {}).get("tone") == "negative"
                                     else 1, -(liq.get(e.get("isin")) or 0)))
        if not rows:
            continue
        out.append(f"\n**{LABEL.get(kind, kind.title())}** ({len(rows)})")
        for e in rows[:PER_TYPE]:
            name = tickers.get(e.get("isin")) or e["company"]
            tone = " _(favourable)_" if e.get("tone") == "favourable" else ""
            if e.get("model_tone"):
                mt = e["model_tone"]
                tone += (f" _({'adverse' if mt['tone'] == 'negative' else 'positive'}"
                         f" per {mt['model']})_ {cite(mt['evidence'])}")
            out.append(f"- **{name}**{tone} — {e['text'][:180]} {cite(e['evidence'])}")
            for df in e.get("document_facts") or []:
                # The headline says "Receipt of order"; the document says how much.
                out.append(f"  - {_doc_fact(df)} {cite(df['evidence'])}")
            if e.get("news_ref"):
                # A clarification filing says nothing; the news it quotes is the story.
                nr = e["news_ref"]
                out.append(f"  - news{' on ' + nr['domain'] if nr['domain'] else ''}: "
                           f"\"{nr['headline'][:160]}\" {cite(nr['evidence'])}")
            if e.get("group"):
                g = e["group"]
                out.append(f"  - group: {g['anchor'] or 'promoter group'}, {g['size']} listed"
                           f" — also {', '.join(g['siblings'])} {cite(g['evidence'])}")
            shown += 1
        if len(rows) > PER_TYPE:
            out.append(f"- …and {len(rows) - PER_TYPE} smaller companies")
    if not events:
        out.append("No high-materiality announcements in this window.")
    out.append("")

    # ---- coming up ----------------------------------------------------------------------
    notable = [u for u in state["upcoming"] if u["type"] != "DIVIDEND"
               or (liq.get(u["isin"]) or 0) >= 1e9]
    if notable:
        out.append("## Coming up (ex-dates in the next 7 days)")
        for u in sorted(notable, key=lambda u: (u["ex_date"], u["type"]))[:15]:
            name = tickers.get(u["isin"]) or u["isin"]
            detail = u["details"].replace("BSE feed: ", "").split(" (scrip")[0]
            out.append(f"- {u['ex_date']} — **{name}**: {detail} {cite(u['evidence'])}")
        out.append("")

    # ---- data quality ------------------------------------------------------------------
    if state.get("quality"):
        out.append("## Data notes")
        for q in state["quality"]:
            out.append(f"- {q}")
        out.append("")

    # ---- sources: grouped by the file each claim came from ---------------------
    # 84 per-claim lines repeating two URLs was unreadable; one line per file is not.
    out.append("## Sources")
    groups: dict[str, list[int]] = {}
    info: dict[str, str] = {}
    for n, eid in enumerate(cite.ids, 1):
        t = ledger.trace(con, eid)
        if t.get("url"):
            key = t["sha256"]
            info[key] = (f"{t['source']} (tier {t['source_tier']}) — [{t['url'][:90]}…]"
                         f"({t['url']}), sha256 `{t['sha256'][:12]}`")
        else:
            key = f"derived:{eid}"
            info[key] = (f"derived by `{t['derivation']}` from {len(t['inputs'])} cited "
                         f"inputs (`{eid}`)")
        groups.setdefault(key, []).append(n)
    for key, nums in groups.items():
        out.append(f"- {_ranges(nums)} {info[key]}")
    out.append("")
    out.append(f"_{len(cite.ids)} claims, each an immutable evidence record; "
               f"`fb trace <evidence_id>` follows any one down to its bytes._")
    return "\n".join(out) + "\n", cite.ids
