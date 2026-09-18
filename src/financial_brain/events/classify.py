"""Deterministic event classification for corporate announcements.

BSE labels every announcement with a category and subcategory. That labelling is the
exchange's own (Tier 1), so it is the primary signal; headline keywords refine it only
where BSE's labels are too coarse - order wins, for example, arrive as "General" or
"Press Release". No model is involved: every classification is reproducible and
explainable by the rule that produced it.

Materiality is the answer to "would an investor holding this want to know today?":

``high``    results, rating actions, auditor resignations, promoter pledges, M&A and
            schemes, fund raising, order wins, key-management changes, clarifications
            of price movement
``medium``  board-meeting notices and outcomes, dividends and record dates, buybacks,
            investor meets and presentations, earnings calls, shareholder meetings
``low``     routine compliance filings

Subcategories surveyed across 2016-2026 (1,472 sampled announcements) drove the table.
"""
from __future__ import annotations

import re

HIGH, MEDIUM, LOW = "high", "medium", "low"

#: (subcategory pattern, event type, materiality) - first match wins.
_BY_SUBCATEGORY: list[tuple[str, str, str]] = [
    (r"financial results|integrated filing \(financial\)|limited review", "RESULTS", HIGH),
    (r"award of order|receipt of order", "ORDER_WIN", HIGH),
    (r"committee of creditors|insolvency|cirp|resolution plan|liquidation",
     "INSOLVENCY", HIGH),
    (r"restructuring", "SCHEME", HIGH),
    (r"issue of securities", "FUND_RAISING", HIGH),
    (r"new listing", "NEW_LISTING", MEDIUM),
    (r"outcome without intimation", "BOARD_OUTCOME", MEDIUM),
    (r"meeting updates", "BOARD_MEETING", MEDIUM),
    (r"reg\.?\s*74\s*\(5\)|reg\.?\s*57\s*\(1\)|amendments to memorandum",
     "COMPLIANCE", LOW),
    (r"resignation of statutory auditor", "AUDITOR_RESIGNATION", HIGH),
    (r"appointment of statutory auditor", "AUDITOR_CHANGE", MEDIUM),
    (r"reg\.?\s*31\s*\(1\)|31\(2\)|encumbrance|pledge", "PROMOTER_PLEDGE", HIGH),
    (r"reg\.?\s*29|sast", "SUBSTANTIAL_ACQUISITION", HIGH),
    (r"closure of trading window", "TRADING_WINDOW", LOW),
    (r"reg\.?\s*7\s*\(|insider trading|\(pit\)", "INSIDER_DISCLOSURE", MEDIUM),
    (r"credit rating", "CREDIT_RATING", HIGH),
    (r"acquisition|takeover", "ACQUISITION", HIGH),
    (r"scheme of arrangement|amalgamation|merger|demerger", "SCHEME", HIGH),
    (r"joint venture", "JOINT_VENTURE", HIGH),
    (r"raising of funds|preferential issue|qualified institutions|qip|rights issue",
     "FUND_RAISING", HIGH),
    (r"allotment of equity|allotment of securities", "ALLOTMENT", MEDIUM),
    (r"esop|esps", "ESOP", LOW),
    (r"clarification", "CLARIFICATION", HIGH),
    (r"resignation of (chief|managing|ceo|cfo|md)|change in management|"
     r"cessation|appointment of (chief|managing|md|ceo|cfo)", "MANAGEMENT_CHANGE", HIGH),
    (r"change in directorate|resignation of director|appointment of director",
     "BOARD_CHANGE", MEDIUM),
    (r"outcome of board meeting", "BOARD_OUTCOME", MEDIUM),
    (r"board meeting", "BOARD_MEETING", MEDIUM),
    (r"buy ?back", "BUYBACK", MEDIUM),
    (r"dividend|record date|book closure", "DIVIDEND", MEDIUM),
    (r"bonus|split|sub-division", "CORPORATE_ACTION", HIGH),
    (r"earnings call transcript", "EARNINGS_CALL", MEDIUM),
    (r"investor presentation", "INVESTOR_PRESENTATION", MEDIUM),
    (r"analyst|investor meet", "INVESTOR_MEET", MEDIUM),
    (r"memorandum of understanding|agreement", "AGREEMENT", MEDIUM),
    (r"agm|egm|postal ballot|court convened", "SHAREHOLDER_MEETING", MEDIUM),
    (r"press release|media release", "PRESS_RELEASE", MEDIUM),
    (r"annual report|brsr|sustainability|secretarial|newspaper|monitoring agency|"
     r"deviation|shareholding|code of conduct|compliance|annexure|address",
     "COMPLIANCE", LOW),
]

#: Headline refinements applied when BSE's label is coarse.
_ORDER_WIN = re.compile(
    r"\b(bags?|secures?|receives?|wins?|awarded|bagged|secured|received|receipt of)\b"
    r".{0,60}"
    r"\b(orders?|contracts?|lo[ai]|letter of (award|intent|acceptance)|work orders?)\b"
    r"|\b(orders?|contracts?)\b.{0,30}\b(worth|valued|of rs|of inr|crore|cr\b)", re.I)
_COARSE = {"GENERAL", "PRESS_RELEASE", "OTHER", "AGREEMENT"}

#: Orders that are *not* business wins: tax demands, penalties, court and regulator
#: orders. Indian companies disclose these constantly under Reg. 30 and they share the
#: word "order" - "received order of revised demand from Deputy Commissioner (Appeals)
#: under Section 73 of the TNGST Act" (APL Apollo, 2026-08-22) is bad news, not a win.
_LEGAL = re.compile(
    # Deliberately not bare "fine", "notice" or "appeal": too common as ordinary words.
    r"\b(demand|penalty|penalt(y|ies)|show[- ]cause|tax|gst|cgst|sgst|igst|income[- ]tax|"
    r"commissioner|assessment order|tribunal|nclt|nclat|high court|supreme court|"
    r"court|sebi order|adjudicat\w*|litigation|arbitration|levied|raid|"
    r"search and seizure|enforcement directorate|demand notice|tax notice|"
    r"imposed a fine|fine of rs)\b", re.I)


def classify(category: str, subcategory: str, headline: str = "",
             subject: str = "") -> tuple[str, str, str]:
    """Return ``(event_type, materiality, rule)`` - ``rule`` records why."""
    sub = " ".join((subcategory or "").split()).lower()
    cat = " ".join((category or "").split()).lower()
    text = f"{headline or ''} {subject or ''}"

    kind, mat, rule = "OTHER", LOW, "no rule matched"
    for pat, k, m in _BY_SUBCATEGORY:
        if sub and re.search(pat, sub):
            kind, mat, rule = k, m, f"subcategory /{pat}/"
            break
    else:
        if sub in ("general", ""):
            kind, mat, rule = "GENERAL", LOW, "subcategory General"
        if "result" in cat:
            kind, mat, rule = "RESULTS", HIGH, "category Result"
        elif "board meeting" in cat:
            kind, mat, rule = "BOARD_MEETING", MEDIUM, "category Board Meeting"
        elif "corp" in cat and "action" in cat:
            kind, mat, rule = "CORPORATE_ACTION", MEDIUM, "category Corp. Action"
        elif "agm" in cat or "egm" in cat:
            kind, mat, rule = "SHAREHOLDER_MEETING", MEDIUM, "category AGM/EGM"
        elif "insider" in cat or "sast" in cat:
            kind, mat, rule = "INSIDER_DISCLOSURE", MEDIUM, "category Insider/SAST"

    # Legal/regulatory orders are checked before order wins, and override even BSE's
    # own "Receipt of Order" label: a tax-demand order filed under that heading is
    # still a tax demand.
    if (kind in _COARSE or kind == "ORDER_WIN") and _LEGAL.search(text):
        return "LEGAL_REGULATORY", HIGH, "headline legal/tax/penalty keywords"
    if kind in _COARSE and _ORDER_WIN.search(text):
        return "ORDER_WIN", HIGH, "headline order/contract keywords"
    return kind, mat, rule
