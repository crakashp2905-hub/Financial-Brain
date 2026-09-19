"""Who filed what about whom: holder -> company edges from exchange disclosures (C24).

BSE writes a templated headline for every takeover-code (SAST) and insider-trading
disclosure, and the template names the filer:

    2011-2017   "<filer> has (now) submitted the disclosure(s) under Reg. 29(2) ..."
    2017-       "The Exchange has received the disclosure under Regulation 29(2) ...
                 Regulations, 2011 [on <date>] for <filer>"

The regulation says what the filer *is*, which is the point of the graph:

``PLEDGE``       Reg. 31(1)/31(2) - encumbrance by a **promoter** (the law applies only
                 to promoters), so the filer is part of the promoter group.
``EXEMPT``       Reg. 10(5)/10(6)/10(7) - an acquisition exempt from an open offer,
                 almost always an inter-se transfer among promoters.
``OPEN_OFFER``   Reg. 18(6) - an acquirer reporting on its open offer.
``SUBSTANTIAL``  Reg. 29(1)/29(2) (and 1997 Reg. 7) - anyone crossing 5% or moving 2%:
                 mutual funds, insurers, lenders, as often as promoters.
``INSIDER``      insider-trading (PIT) disclosures.

Only PLEDGE and EXEMPT are promoter evidence. A fund crossing 5% in forty companies
must never make those forty companies one "group".
"""
from __future__ import annotations

import re

_REG = [
    (re.compile(r"31\s*\(1\)|31\s*\(2\)|encumbrance|pledge", re.I), "PLEDGE"),
    (re.compile(r"reg(?:ulation)?\.?\s*10\s*\(\s*[567]\s*\)", re.I), "EXEMPT"),
    (re.compile(r"reg(?:ulation)?\.?\s*18\s*\(\s*6\s*\)", re.I), "OPEN_OFFER"),
    (re.compile(r"reg(?:ulation)?\.?\s*29\s*\(|regulations?,? 1997|reg\.?\s*7\s*\(",
                re.I), "SUBSTANTIAL"),
    (re.compile(r"insider|\(pit\)|reg\.?\s*13\s*\(", re.I), "INSIDER"),
]
PROMOTER_RELATIONS = {"PLEDGE", "EXEMPT"}

_SUBMITTED = re.compile(r"^(.+?)\s+has\s+(?:now\s+)?submitted\s+the\s+disclosures?", re.I)
_RECEIVED = re.compile(r"the exchange has (?:now )?received.*?\bfor\s+(.+)$", re.I)
_PAC = re.compile(r"\s*(?:,|&|\band\b|\balongwith\b|\balong with\b)?\s*(?:its\s+|the\s+)?"
                  r"(?:pacs?|persons? acting in concert|others|other promoters?|"
                  r"promoter group|associates)\.?\s*$", re.I)
_LENDER = re.compile(r"\s*\(?\s*lender\s*:.*$", re.I)
_SUFFIX = [(r"\bprivate\b", "pvt"), (r"\blimited\b", "ltd"), (r"\bcompany\b", "co"),
           (r"\bcorporation\b", "corp"), (r"\b(m/s|mr|mrs|ms|shri|smt|dr)\b\.?", "")]
_ORG = re.compile(r"\b(ltd|llp|pvt|trust|inc|corp|co|fund|bank|plc|llc|gmbh|sa|ag|"
                  r"holdings?|investments?|ventures?|enterprises?|trustees?|"
                  r"insurance|capital|securities|finance|foundation|huf)\b", re.I)
_INSTITUTION = re.compile(r"mutual fund|asset management|\bamc\b|insurance|"
                          r"\blic\b|\bbank\b|trustee(ship)?|\bfpi\b|\bfii\b|"
                          r"investment trust|pension|sovereign|provident fund|"
                          r"\bnbfc\b|financial services|stock broking|"
                          r"asset reconstruction", re.I)
_GENERIC = {"", "PROMOTER", "PROMOTERS", "PROMOTER GROUP", "OTHERS", "PACS", "PAC",
            "THE PROMOTERS", "PROMOTERS AND PROMOTER GROUP", "ACQUIRER", "ACQUIRERS"}


def relation(subcategory: str, headline: str = "") -> str | None:
    text = f"{subcategory} {headline}"
    for pat, rel in _REG:
        if pat.search(text):
            return rel
    return None


def filer(headline: str) -> str | None:
    """The filer named in a templated disclosure headline, or None if it names none
    (truncated, or a company-written free-text notice)."""
    h = " ".join((headline or "").split())
    m = _SUBMITTED.match(h)
    received = False
    if not m:
        m, received = _RECEIVED.search(h), True
    if not m:
        return None
    name = m.group(1).strip(" .:;,-")
    if received:
        # "... 2011 for January 04, 2022 for Spank Management" - the filer follows the
        # last "for"; a date is never a filer.
        name = re.split(r"\bfor\s+", name)[-1].strip(" .:;,-")
    if h.rstrip().endswith("..") or re.fullmatch(r"[A-Za-z]+ \d{1,2}, \d{4}", name):
        return None
    name = _LENDER.sub("", name)
    for _ in range(2):
        name = _PAC.sub("", name).strip(" .:;,-&")
    return name or None


def key(name: str) -> str:
    """Normalised identity for a filer name: case, punctuation, legal-suffix spelling."""
    k = name.lower().replace("&", " and ")
    k = re.sub(r"[^a-z0-9/ ]", " ", k)
    for pat, rep in _SUFFIX:
        k = re.sub(pat, rep, k)
    k = re.sub(r"[^a-z0-9 ]", " ", k)
    return " ".join(k.upper().split())


def kind(name: str) -> str:
    if _INSTITUTION.search(name):
        return "institution"
    return "organisation" if _ORG.search(name) else "person"


def usable(k: str) -> bool:
    return k not in _GENERIC and len(k) >= 4
