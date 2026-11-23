"""AMFI - the official daily NAV feed for every Indian mutual fund scheme (C02).

AMFI publishes one plain-text file with the current NAV of all ~18k schemes, ISINs
included, free and without authentication:

    Scheme Code;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;
    Option;Net Asset Value;Date

The file interleaves section headers with data rows - a scheme-type line
("Open Ended Schemes(Equity Scheme - Large Cap Fund)"), then a fund-house line
("Axis Mutual Fund"), then that house's schemes - so the parser carries both down the
file. Each row states its own date; the file is "as published", not "as of a date we
asked for", so rows are stored under the date they carry, never the date we fetched.

Tier 1: AMFI is the industry body that publishes these NAVs, not a scraper of them.
"""
from __future__ import annotations

from datetime import date, datetime
import re

from .base import FetchError, FetchResult, Provider

NAV_ALL = "https://portal.amfiindia.com/spages/NAVAll.txt"
HEADER = "Scheme Code"


class AmfiNav(Provider):
    source = "AMFI"
    dataset = "mf_nav"
    tier = 1

    def fetch(self, business_date: date) -> FetchResult:
        payload, status, ctype = self._http_get(NAV_ALL, timeout=120)
        if HEADER.encode() not in payload[:200]:
            raise FetchError(f"NAVAll.txt did not start with the expected header: "
                             f"{payload[:80]!r}", retryable=True, status=status)
        return FetchResult(payload=payload, url=NAV_ALL, retrieved_at=self._now(),
                           filename=f"NAVAll-{business_date.isoformat()}.txt",
                           content_type=ctype, http_status=status)


def _decode(payload: bytes) -> str:
    """AMFI publishes UTF-8 today. Try it strictly, then fall back to Windows-1252, which
    older exports of this file used - so a change of encoding degrades rather than
    raising mid-ingest."""
    for encoding in ("utf-8", "cp1252"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def parse(payload: bytes) -> list[dict]:
    """Rows of the NAV file, with the fund house and scheme type each row sits under.

    Rows whose NAV is not a number ("N.A." for a scheme that did not declare one) are
    dropped rather than stored as null: a missing NAV is an absence, not a price.
    """
    fund_house = scheme_type = None
    out: list[dict] = []
    for raw in _decode(payload).splitlines():
        line = raw.strip()
        if not line or line.startswith(HEADER):
            continue
        if ";" not in line:
            # A section header: scheme types name themselves, everything else is a house.
            if "Scheme" in line and "(" in line:
                scheme_type = line
            else:
                fund_house = line
            continue
        parts = [p.strip() for p in line.split(";")]
        if len(parts) < 8:
            continue
        code, isin_g, isin_r, name, plan, option, nav, nav_date = parts[:8]
        try:
            value = float(nav)
        except ValueError:
            continue
        try:
            on = datetime.strptime(nav_date, "%d-%b-%Y").date()
        except ValueError:
            continue
        out.append({"scheme_code": code, "isin_growth": isin_g if isin_g != "-" else None,
                    "isin_reinvest": isin_r if isin_r != "-" else None,
                    "scheme_name": name, "fund_house": fund_house,
                    "scheme_type": scheme_type, "plan": plan or None,
                    "option": option or None, "nav": value, "nav_date": on})
    return out


def classify_scheme(row: dict) -> dict[str, str]:
    """Conservatively normalize AMFI descriptors without filling unknown attributes by guess."""
    plan_text = " ".join(str(row.get(k) or "") for k in ("plan", "scheme_name")).upper()
    if re.search(r"\bDIRECT\b", plan_text):
        plan_type = "DIRECT"
    elif re.search(r"\bREGULAR\b", plan_text):
        plan_type = "REGULAR"
    else:
        plan_type = "UNCLASSIFIED"

    option_text = " ".join(str(row.get(k) or "") for k in ("option", "scheme_name")).upper()
    if "REINVEST" in option_text:
        payout_type = "IDCW_REINVESTMENT"
    elif re.search(r"\b(PAYOUT|DIVIDEND)\b|\bIDCW\b", option_text):
        payout_type = "IDCW_PAYOUT" if "PAYOUT" in option_text or "DIVIDEND" in option_text else "IDCW_UNSPECIFIED"
    elif re.search(r"\bGROWTH\b", option_text):
        payout_type = "GROWTH"
    else:
        payout_type = "UNCLASSIFIED"

    text = " ".join(str(row.get(k) or "") for k in ("scheme_type", "scheme_name")).upper()
    if "INTERVAL" in text:
        structure_type = "INTERVAL"
    elif "CLOSE ENDED" in text or "CLOSED ENDED" in text:
        structure_type = "CLOSED_ENDED"
    elif "OPEN ENDED" in text:
        structure_type = "OPEN_ENDED"
    else:
        structure_type = "UNCLASSIFIED"

    if "FUND OF FUNDS" in text or re.search(r"\bFOF\b", text):
        asset_class = "FUND_OF_FUNDS"
    elif re.search(r"\b(EQUITY|LARGE CAP|MID CAP|SMALL CAP|ELSS)\b", text):
        asset_class = "EQUITY"
    elif re.search(r"\b(DEBT|GILT|BOND|LIQUID|MONEY MARKET|FIXED INCOME)\b", text):
        asset_class = "DEBT"
    elif "HYBRID" in text or "BALANCED" in text:
        asset_class = "HYBRID"
    elif re.search(r"\b(GOLD|SILVER|COMMODITY)\b", text):
        asset_class = "COMMODITY"
    else:
        asset_class = "UNCLASSIFIED"

    if re.search(r"\b(INDEX|PASSIVE|ETF|EXCHANGE TRADED)\b", text):
        management_type = "PASSIVE"
    elif "ACTIVELY MANAGED" in text:
        management_type = "ACTIVE"
    else:
        management_type = "UNCLASSIFIED"
    return {"plan_type": plan_type, "payout_type": payout_type,
            "structure_type": structure_type, "management_type": management_type,
            "asset_class": asset_class}
