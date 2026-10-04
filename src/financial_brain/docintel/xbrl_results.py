"""Read an exchange XBRL results instance into numbers, by context, never by position.

The values are tagged, so there is nothing to locate and nothing to OCR; what needs care is *which
number*. One file carries the current period, the year to date, and dozens of segment and note
contexts with their own copies of the same concept. Only facts in a context with **no dimensional
scenario** are the company-level figures:

    OneD   the current reporting period (the quarter, for a quarterly filing)
    FourD  the year to date / cumulative figure
    OneI   the balance-sheet instant at the period end
    FourI  the instant for the comparative / year-end

Segment revenue sits in ``OneReportableSegmentRevenue01D`` and friends, which have a scenario and are
deliberately ignored: adding them to ``OneD`` would double count.

Banks file in a different shape (``InterestEarned``, ``ProfitLossForThePeriod``) so the canonical map
carries fallbacks, and a statement is only marked arithmetically sound when the figures that *should*
add up do. XBRL is validated by the exchange, so a failure here is a finding about the filing or the
map, not noise to be tolerated.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

NS_FIN = "http://www.bseindia.com/xbrl/fin/"
NS_XBRLI = "http://www.xbrl.org/2003/instance"
CTX = {"OneD": "Q", "FourD": "YTD", "OneI": "I", "FourI": "I_PRIOR"}

#: canonical field -> tags, tried in order. Non-bank first, then the bank-format names.
CANON = {
    "revenue": ("RevenueFromOperations", "InterestEarned"),
    "other_income": ("OtherIncome",),
    "total_income": ("Income",),
    "total_expenses": ("Expenses", "ExpenditureExcludingProvisionsAndContingencies"),
    "pbt": ("ProfitBeforeTax", "ProfitLossFromOrdinaryActivitiesBeforeTax"),
    "pat": ("ProfitLossForPeriod", "ProfitLossForThePeriod"),
    "eps_basic": ("BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
                  "BasicEarningsPerShareAfterExtraordinaryItems"),
}


class XBRLError(ValueError):
    pass


@dataclass
class Instance:
    facts: dict = field(default_factory=dict)       #: {ctx_label: {concept: float}}
    text: dict = field(default_factory=dict)         #: non-numeric company-level facts
    period: tuple | None = None                      #: (start, end) of OneD

    def current(self) -> dict:
        return self.facts.get("Q", {})

    def canonical(self, ctx: str = "Q") -> dict:
        f = self.facts.get(ctx, {})
        out = {}
        for name, tags in CANON.items():
            for t in tags:
                if t in f:
                    out[name] = f[t]
                    break
        return out


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse(payload: bytes) -> Instance:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as e:
        raise XBRLError(f"not well-formed XML: {e}") from e
    plain: dict[str, tuple] = {}                     # context id -> (start, end) for scenario-free ones
    dimensional: set[str] = set()
    for c in root.iter(f"{{{NS_XBRLI}}}context"):
        if c.find(f"{{{NS_XBRLI}}}scenario") is not None:
            dimensional.add(c.get("id"))
            continue
        p = c.find(f"{{{NS_XBRLI}}}period")
        if p is None:
            continue
        s, e, i = (p.find(f"{{{NS_XBRLI}}}{k}") for k in ("startDate", "endDate", "instant"))
        plain[c.get("id")] = ((s.text, e.text) if s is not None and e is not None
                              else (i.text, i.text) if i is not None else None)
    inst = Instance()
    # The company-level contexts are recognised by their reserved ids. Filings of about 2020-2023 reference
    # OneD/FourD without ever defining them (only the dimensional segment contexts are defined), so
    # requiring a definition rejected most of the archive; the id is the contract, a scenario is the tell.
    for el in root:
        if not el.tag.startswith("{" + NS_FIN):
            continue
        ref = el.get("contextRef")
        label = CTX.get(ref)
        if label is None or ref in dimensional:
            continue
        name, text = _local(el.tag), (el.text or "").strip()
        if el.get("unitRef") is None:
            if label == "Q" and text:
                inst.text[name] = text
            continue
        try:
            v = float(text)
        except ValueError:
            continue
        inst.facts.setdefault(label, {})[name] = v
    inst.period = plain.get("OneD")
    if inst.period is None:                         # undefined context: the filing states its own dates
        a, b = inst.text.get("DateOfStartOfReportingPeriod"), inst.text.get("DateOfEndOfReportingPeriod")
        inst.period = (a, b) if a and b else None
    if not inst.facts.get("Q"):
        raise XBRLError("no company-level current-period facts (no OneD context)")
    return inst


def checks(canon: dict) -> dict[str, bool]:
    """The statement's own arithmetic, to the rounding the filing declares (about 1 in 1e3)."""
    def close(a, b):
        return abs(a - b) <= max(1.0, abs(b) * 0.002)
    out = {}
    if {"revenue", "other_income", "total_income"} <= canon.keys():
        out["income adds up"] = close(canon["revenue"] + canon["other_income"], canon["total_income"])
    return out
