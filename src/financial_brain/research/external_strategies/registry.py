"""Source registry for externally inspired algorithms (ALG-001..011, ALG-074, ALG-075, ALG-110).

The registry is passive documentation. It records where an idea came from, what rights are known, and whether the
entry is a strategy or only an indicator. Nothing here downloads, imports or runs third-party code, and no
registry entry can make a claim about performance. Rights default to ``reference_only`` unless a licence is
independently established and recorded.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from financial_brain.patterns.spec import canonical_json

PLATFORMS = frozenset({"TradingView", "MQL5 Articles", "MQL5 CodeBase"})
SOURCE_TYPES = frozenset({"strategy", "indicator", "article", "concept"})
RIGHTS = frozenset({"reference_only", "licence_verified"})
CLAIM_LABEL = "SOURCE_CLAIM"  # the only label a vendor performance figure may carry (ALG-007)
FORBIDDEN_EDGE_LABELS = frozenset({"EDGE_CONFIRMED", "PROVEN", "PROFITABLE"})
INCOMPATIBLE_MARKETS = frozenset({"US_EQUITY", "LONDON_FX", "ASIA_FX", "CRYPTO_PERP", "FOREX_CFD"})
_ID = re.compile(r"^[A-Z]{2,3}-\d{3}$")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REQUIRED = ("id", "title", "author", "platform", "url", "access_date", "source_type", "citation", "rights_status")


class RegistryError(ValueError):
    """A registry entry or the registry as a whole fails validation."""


@dataclass(frozen=True)
class SourceEntry:
    id: str
    title: str
    author: str
    platform: str
    url: str
    access_date: str
    source_type: str
    citation: str
    rights_status: str
    market: str = "INDIA_NSE"
    family: str = "unclassified"
    unsuitable_markets: tuple[str, ...] = ()

    @property
    def paper_only(self) -> bool:
        """Every external-source algorithm is paper-only (ALG-081). Never routable to a live order."""
        return True

    def claim_label(self) -> str:
        return CLAIM_LABEL

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "author": self.author,
            "platform": self.platform,
            "url": self.url,
            "access_date": self.access_date,
            "source_type": self.source_type,
            "citation": self.citation,
            "rights_status": self.rights_status,
            "market": self.market,
            "family": self.family,
            "unsuitable_markets": list(self.unsuitable_markets),
        }


def validate_entry(raw: dict[str, Any]) -> SourceEntry:
    """Reject missing provenance, bad identifiers and unknown categories (ALG-001, ALG-002, ALG-110)."""
    missing = [k for k in REQUIRED if not raw.get(k)]
    if missing:
        raise RegistryError(f"{raw.get('id', '?')}: missing provenance fields {missing}")
    if not _ID.match(str(raw["id"])):
        raise RegistryError(f"bad id {raw['id']!r}")
    if raw["platform"] not in PLATFORMS:
        raise RegistryError(f"{raw['id']}: unknown platform {raw['platform']!r}")
    if raw["source_type"] not in SOURCE_TYPES:
        raise RegistryError(f"{raw['id']}: unknown source_type {raw['source_type']!r}")
    if raw["rights_status"] not in RIGHTS:
        raise RegistryError(f"{raw['id']}: unknown rights_status {raw['rights_status']!r}")
    if not _ISO.match(str(raw["access_date"])):
        raise RegistryError(f"{raw['id']}: access_date must be YYYY-MM-DD")
    date.fromisoformat(str(raw["access_date"]))
    if not str(raw["url"]).startswith("https://"):
        raise RegistryError(f"{raw['id']}: url must be https")
    # ALG-002: an indicator is never labelled as an executable strategy.
    if raw["source_type"] == "indicator" and raw.get("executable") is True:
        raise RegistryError(f"{raw['id']}: an indicator cannot be an executable strategy")
    # ALG-004 and ALG-003: rights stay reference_only unless a licence is recorded.
    if raw["rights_status"] == "licence_verified" and not raw.get("licence_reference"):
        raise RegistryError(f"{raw['id']}: licence_verified needs a licence_reference")
    unsuitable = tuple(raw.get("unsuitable_markets", ()))
    bad = [m for m in unsuitable if m not in INCOMPATIBLE_MARKETS]
    if bad:
        raise RegistryError(f"{raw['id']}: unknown market tags {bad}")
    return SourceEntry(
        id=str(raw["id"]), title=str(raw["title"]), author=str(raw["author"]), platform=str(raw["platform"]),
        url=str(raw["url"]), access_date=str(raw["access_date"]), source_type=str(raw["source_type"]),
        citation=str(raw["citation"]), rights_status=str(raw["rights_status"]),
        market=str(raw.get("market", "INDIA_NSE")), family=str(raw.get("family", "unclassified")),
        unsuitable_markets=unsuitable,
    )


def validate_registry(rows: list[dict[str, Any]]) -> list[SourceEntry]:
    """Validate every entry and reject duplicate IDs (ALG-001)."""
    seen: set[str] = set()
    out: list[SourceEntry] = []
    for raw in rows:
        entry = validate_entry(raw)
        if entry.id in seen:
            raise RegistryError(f"duplicate id {entry.id}")
        seen.add(entry.id)
        out.append(entry)
    return out


def registry_hash(entries: list[SourceEntry]) -> str:
    """Deterministic digest: any change to a source snapshot changes this value (ALG-008)."""
    body = [e.to_dict() for e in sorted(entries, key=lambda e: e.id)]
    return hashlib.sha256(canonical_json(body)).hexdigest()


def load_registry(path: Path) -> list[SourceEntry]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "entries" not in data:
        raise RegistryError("registry file must be an object with an entries list")
    return validate_registry(list(data["entries"]))


def display_label(entry: SourceEntry, *, claimed_return: bool) -> str:
    """Vendor figures render as SOURCE_CLAIM only; no label here can assert an edge (ALG-007, ALG-106)."""
    if claimed_return:
        return CLAIM_LABEL
    return "SOURCE_REFERENCE"


def assert_no_edge_label(label: str) -> None:
    if label in FORBIDDEN_EDGE_LABELS:
        raise RegistryError(f"label {label} cannot be produced from a source; evidence must be independent")


def stale_check(entry: SourceEntry, *, as_of: date, max_age_days: int) -> bool:
    """True when the entry's last access is older than the allowed age (ALG-075)."""
    return (as_of - date.fromisoformat(entry.access_date)).days > max_age_days
