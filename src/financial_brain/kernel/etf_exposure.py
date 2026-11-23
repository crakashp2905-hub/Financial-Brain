"""Conservative ETF exposure grouping from an explicit exchange reference label.

These groups are not benchmark mappings and must not be treated as interchangeable
returns. Unknown labels are retained as OTHER_LISTED/UNCLASSIFIED with low confidence;
they never silently become broad-market equity.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ETFExposureClass(str, Enum):
    EQUITY_BROAD_MARKET = "EQUITY_BROAD_MARKET"
    EQUITY_SECTOR = "EQUITY_SECTOR"
    EQUITY_FACTOR = "EQUITY_FACTOR"
    BOND_DEBT = "BOND_DEBT"
    GOLD = "GOLD"
    SILVER = "SILVER"
    INTERNATIONAL = "INTERNATIONAL"
    OTHER_LISTED = "OTHER_LISTED"
    UNCLASSIFIED = "UNCLASSIFIED"


@dataclass(frozen=True)
class ETFExposureClassification:
    exposure_class: ETFExposureClass
    rule_id: str
    confidence: str
    matched_text: str | None


_RULES: tuple[tuple[str, ETFExposureClass, tuple[str, ...]], ...] = (
    ("GOLD_LABEL", ETFExposureClass.GOLD, ("GOLD",)),
    ("SILVER_LABEL", ETFExposureClass.SILVER, ("SILVER",)),
    ("INTERNATIONAL_LABEL", ETFExposureClass.INTERNATIONAL, (
        "INTERNATIONAL", "NASDAQ", "S&P 500", "S&P500", "MSCI WORLD", "MSCI CHINA",
        "MSCI JAPAN", "MSCI USA", "HANG SENG", "HONG KONG", "UNITED STATES", "USA",
        "GLOBAL", "WORLD",
    )),
    ("FIXED_INCOME_LABEL", ETFExposureClass.BOND_DEBT, (
        "BOND", "DEBT", "GILT", "TREASURY", "GOVERNMENT SECURITIES", "SDL",
        "LIQUID", "OVERNIGHT",
    )),
    ("FACTOR_LABEL", ETFExposureClass.EQUITY_FACTOR, (
        "EQUAL WEIGHT", "EQUAL-WEIGHT", "MOMENTUM", "QUALITY", "VALUE", "LOW VOL",
        "LOW VOLATILITY", "ALPHA", "FACTOR",
    )),
    ("SECTOR_LABEL", ETFExposureClass.EQUITY_SECTOR, (
        "BANK", "BANKING", "FINANCIAL", "NIFTY IT", "INFORMATION TECHNOLOGY", "PHARMA",
        "HEALTHCARE", "AUTO", "AUTOMOBILE", "FMCG", "METAL", "REALTY",
        "INFRASTRUCTURE", "ENERGY", "PSU", "CONSUMPTION", "MEDIA",
    )),
    ("BROAD_INDEX_LABEL", ETFExposureClass.EQUITY_BROAD_MARKET, (
        "NIFTY 50", "NIFTY50", "NIFTY 100", "NIFTY100", "NIFTY 200", "NIFTY200",
        "NIFTY 500", "NIFTY500", "NIFTY NEXT 50", "SENSEX", "MID CAP", "MIDCAP",
        "SMALL CAP", "SMALLCAP", "BROAD MARKET",
    )),
)


def _contains_label(text: str, label: str) -> bool:
    """Match a label as a phrase, with alphanumeric boundaries around its ends."""
    start = 0
    while (index := text.find(label, start)) >= 0:
        before = text[index - 1] if index else " "
        end = index + len(label)
        after = text[end] if end < len(text) else " "
        if not before.isalnum() and not after.isalnum():
            return True
        start = index + 1
    return False


def classify_etf(underlying: str | None, security_name: str | None) -> ETFExposureClassification:
    """Classify only recognizable labels; prefer the exchange's underlying field."""
    underlying_text = " ".join((underlying or "").upper().split())
    name_text = " ".join((security_name or "").upper().split())
    text = underlying_text or name_text
    if not text:
        return ETFExposureClassification(
            ETFExposureClass.UNCLASSIFIED, "NO_EXPOSURE_LABEL", "UNCLASSIFIED", None
        )

    for rule_id, exposure, labels in _RULES:
        for label in labels:
            if _contains_label(text, label):
                return ETFExposureClassification(exposure, rule_id, "HEURISTIC_LABEL", label)
    if underlying_text:
        return ETFExposureClassification(
            ETFExposureClass.OTHER_LISTED, "UNMAPPED_UNDERLYING_LABEL", "REVIEW_REQUIRED",
            underlying_text,
        )
    return ETFExposureClassification(
        ETFExposureClass.UNCLASSIFIED, "NAME_ONLY_UNMAPPED", "REVIEW_REQUIRED", name_text
    )
