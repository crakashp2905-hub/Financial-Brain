"""Validated, typed view of the Nison/Bulkowski pattern catalogue (CP-0014..CP-0025).

The catalogue is research input, not a trading rule set. Loading it never
upgrades a definition: every entry stays ``definition_status = descriptive``
until formal evidence exists, and historical book statistics are kept with
their conditioning caveats.

CLI: ``python -m financial_brain.patterns.catalogue <path.json>`` exits 1 on any
validation error and otherwise prints per-source and per-family counts.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

REQUIRED_KEYS = frozenset(
    {
        "id",
        "source",
        "name",
        "family",
        "direction",
        "definition",
        "confirmation",
        "book_stats",
        "caveat",
        "repo_status",
        "data_needed",
    }
)
SOURCE_PREFIX = {"nison": "N", "bulkowski": "B"}
ID_PATTERN = re.compile(r"^(N|B)\d{2,3}$")

# Controlled family taxonomy (CP-0019). Rows whose source family is not a real
# family (e.g. "either", "bearish") map to UNCLASSIFIED rather than being guessed.
FAMILY_MAP: dict[str, str] = {
    "candle": "candle",
    "reversal": "reversal",
    "consolidation": "consolidation",
    "continuation": "continuation",
    "multi-bar": "multi_bar",
    "gap": "gap",
    "range": "range",
    "level": "level",
    "indicator": "indicator",
    "count": "count",
    "structure": "structure",
    "reversal/consolidation": "compound",
    "consolidation/reversal": "compound",
    "candle/range": "compound",
    "reversal/gap": "compound",
    "either": "unclassified",
    "bearish": "unclassified",
    "bearish/either": "unclassified",
}
FAMILIES = frozenset(FAMILY_MAP.values()) | {"unclassified"}

# Directional class (CP-0020). Descriptive direction only; never a trade side.
DIRECTION_MAP: dict[str, str] = {
    "up": "bullish",
    "bullish reversal": "bullish",
    "down": "bearish",
    "bearish reversal": "bearish",
    "either": "either",
}
DIRECTION_CLASSES = frozenset({"bullish", "bearish", "neutral", "either", "unclassified"})

DEFINITION_STATUSES = ("descriptive", "formalized", "implemented", "fixture_verified", "paper_eligible")
# Text alone can only ever establish "descriptive" (CP-0021).
INGEST_DEFINITION_STATUS = "descriptive"

_NUMBER = re.compile(r"^\d+(\.\d+)?$")
_STAT_FIELDS = ("n", "failure_pct", "avg_move_pct", "likely_move_pct", "target_hit_pct")


@dataclass(frozen=True)
class TypedStat:
    """A book statistic. ``value`` is set only for a clean plain number (CP-0016)."""

    value: float | int | None
    source_claim_text: str | None


@dataclass(frozen=True)
class CatalogueEntry:
    id: str
    source: str
    name: str
    family_raw: str
    family: str
    direction_raw: str
    direction_class: str
    definition_status: str
    sample_total: int | None
    stats: dict[str, TypedStat]
    basis: str | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)
    # CP-0015: every book statistic is conditional and historical; CP-0017: references stay unverified;
    # CP-0022: thresholds and lags are unset until precisely specified.
    claim_kind: str = "none"
    reference: dict[str, Any] = field(default_factory=dict)
    parameters: dict[str, Any] | None = None
    knowledge_lag: int | None = None
    # CP-0023: source-quality fields. Every value starts UNASSESSED; nothing in the source notes assesses them yet.
    source_selection_bias: str = "UNASSESSED"
    lookahead_risk: str = "UNASSESSED"
    source_market: str = "UNASSESSED"
    source_period: str = "UNASSESSED"


@dataclass
class CatalogueReport:
    entries: list[CatalogueEntry]
    errors: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors

    def counts_by_source(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for entry in self.entries:
            out[entry.source] = out.get(entry.source, 0) + 1
        return dict(sorted(out.items()))

    def counts_by_family(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for entry in self.entries:
            out[entry.family] = out.get(entry.family, 0) + 1
        return dict(sorted(out.items()))


def _parse_stat(value: Any) -> TypedStat:
    if value is None:
        return TypedStat(None, None)
    text = str(value).strip()
    if _NUMBER.match(text):
        number: float | int = int(text) if "." not in text else float(text)
        return TypedStat(number, text)
    return TypedStat(None, text)


def _validate_entry(raw: dict[str, Any], seen: set[str], errors: list[str]) -> CatalogueEntry | None:
    ident = raw.get("id", "<missing id>")
    missing = sorted(REQUIRED_KEYS - set(raw))
    if missing:
        errors.append(f"{ident}: missing keys {missing}")
        return None
    source = raw["source"]
    if source not in SOURCE_PREFIX:
        errors.append(f"{ident}: unknown source {source!r}")
        return None
    if not isinstance(ident, str) or not ID_PATTERN.match(ident):
        errors.append(f"{ident}: id must match {ID_PATTERN.pattern}")
        return None
    if ident[0] != SOURCE_PREFIX[source]:
        errors.append(f"{ident}: prefix does not match source {source!r}")
        return None
    if ident in seen:
        errors.append(f"{ident}: duplicate id")
        return None
    seen.add(ident)

    family_raw = str(raw["family"])
    family = FAMILY_MAP.get(family_raw)
    if family is None:
        errors.append(f"{ident}: unmapped family {family_raw!r}")
        return None

    direction_raw = str(raw["direction"])
    direction_class = DIRECTION_MAP.get(direction_raw, "unclassified")

    stats_raw = raw["book_stats"]
    stats: dict[str, TypedStat] = {}
    basis = None
    sample_total = None
    if stats_raw is not None:
        if not isinstance(stats_raw, dict):
            errors.append(f"{ident}: book_stats must be an object or null")
            return None
        unknown = sorted(set(stats_raw) - set(_STAT_FIELDS) - {"basis"})
        if unknown:
            errors.append(f"{ident}: unknown book_stats keys {unknown}")
            return None
        for key in _STAT_FIELDS:
            if key in stats_raw:
                stats[key] = _parse_stat(stats_raw[key])
        basis = stats_raw.get("basis")
        n_stat = stats.get("n")
        if n_stat and isinstance(n_stat.value, int):
            sample_total = n_stat.value

    return CatalogueEntry(
        id=ident,
        source=source,
        name=str(raw["name"]),
        family_raw=family_raw,
        family=family,
        direction_raw=direction_raw,
        direction_class=direction_class,
        definition_status=INGEST_DEFINITION_STATUS,
        sample_total=sample_total,
        stats=stats,
        basis=basis,
        raw=raw,
        claim_kind="historical_conditional" if stats else "none",
        reference={"source_title": source, "edition": None, "chapter": None, "page_unverified": True},
        parameters=None,
        knowledge_lag=None,
    )


def validate_catalogue(data: dict[str, Any], *, expected_count: int | None = None) -> CatalogueReport:
    """Validate a parsed catalogue document. Never raises for malformed rows."""
    errors: list[str] = []
    rows = data.get("patterns")
    if not isinstance(rows, list):
        return CatalogueReport([], ["document has no 'patterns' list"])
    seen: set[str] = set()
    entries: list[CatalogueEntry] = []
    for raw in rows:
        if not isinstance(raw, dict):
            errors.append(f"non-object row: {raw!r}")
            continue
        entry = _validate_entry(raw, seen, errors)
        if entry is not None:
            entries.append(entry)
    if expected_count is not None and len(rows) != expected_count:
        errors.append(f"expected {expected_count} entries, found {len(rows)}")
    return CatalogueReport(entries, errors)


def load_catalogue(path: Path) -> CatalogueReport:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        return CatalogueReport([], ["top-level JSON must be an object"])
    return validate_catalogue(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the chart-pattern catalogue.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--expect", type=int, default=None, help="expected number of entries")
    args = parser.parse_args(argv)
    try:
        with args.path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read catalogue: {exc}", file=sys.stderr)
        return 1
    report = validate_catalogue(data, expected_count=args.expect)
    print(f"entries: {len(report.entries)}")
    print(f"by source: {report.counts_by_source()}")
    print(f"by family: {report.counts_by_family()}")
    for message in report.errors:
        print(f"ERROR: {message}", file=sys.stderr)
    return 0 if report.ok else 1


class PromotionRefused(ValueError):
    """A definition status was raised without evidence or out of sequence."""


class MissingParameters(ValueError):
    """A detector or rule needs thresholds that the catalogue never fixed."""


def require_parameters(entry: CatalogueEntry) -> dict[str, Any]:
    """Undefined thresholds cannot silently fall back to fitted defaults (CP-0022)."""
    if entry.parameters is None:
        raise MissingParameters(f"{entry.id}: parameters are unset; a detector must declare them explicitly")
    return entry.parameters


def promote_definition_status(entry: CatalogueEntry, target: str, evidence: str | None) -> CatalogueEntry:
    """Advance ``definition_status`` one step at a time, only with recorded evidence (CP-0021).

    Text alone can only ever hold ``descriptive``. Any higher status needs a
    non-empty evidence reference and must follow the previous status directly.
    """
    if target not in DEFINITION_STATUSES:
        raise ValueError(f"unknown definition status {target!r}")
    current = DEFINITION_STATUSES.index(entry.definition_status)
    wanted = DEFINITION_STATUSES.index(target)
    if wanted == current:
        return entry
    if wanted < current:
        raise PromotionRefused(f"{entry.id}: cannot demote {entry.definition_status} to {target}")
    if wanted != current + 1:
        raise PromotionRefused(f"{entry.id}: must advance one step from {entry.definition_status}")
    if not evidence or not evidence.strip():
        raise PromotionRefused(f"{entry.id}: promotion to {target} requires an evidence reference")
    return replace(entry, definition_status=target)


if __name__ == "__main__":
    raise SystemExit(main())


# ---------------------------------------------------------------- versioning and migration (CP-0024)
CATALOGUE_SCHEMA_VERSION = "1"
SOURCE_QUALITY_LEVELS = frozenset({"UNASSESSED", "LOW", "MEDIUM", "HIGH"})


class MigrationRefused(ValueError):
    """A migration would change the meaning or identity of an existing stable ID."""


def migrate_document(data: dict[str, Any]) -> dict[str, Any]:
    """Bring a catalogue document to :data:`CATALOGUE_SCHEMA_VERSION` without changing any stable ID (CP-0024).

    A document with no ``schema_version`` is treated as version 0. The input is not mutated. Migration refuses
    if any pattern ID would be dropped, renamed or duplicated.
    """
    version = str(data.get("schema_version", "0"))
    if version not in ("0", CATALOGUE_SCHEMA_VERSION):
        raise MigrationRefused(f"unknown schema_version {version!r}")
    rows = data.get("patterns")
    if not isinstance(rows, list):
        raise MigrationRefused("document has no pattern list")
    ids_before = [r.get("id") for r in rows if isinstance(r, dict)]
    migrated = dict(data)
    migrated["schema_version"] = CATALOGUE_SCHEMA_VERSION
    migrated["patterns"] = [dict(r) for r in rows]
    ids_after = [r.get("id") for r in migrated["patterns"]]
    if ids_before != ids_after or len(set(ids_after)) != len(ids_after):
        raise MigrationRefused("migration would change or duplicate stable IDs")
    return migrated


def source_quality(entry: CatalogueEntry) -> dict[str, str]:
    """The four source-quality fields as a plain mapping, validated against the allowed levels."""
    for name in ("source_selection_bias", "lookahead_risk"):
        value = getattr(entry, name)
        if value not in SOURCE_QUALITY_LEVELS:
            raise ValueError(f"{entry.id}: {name} must be one of {sorted(SOURCE_QUALITY_LEVELS)}")
    return {
        "source_selection_bias": entry.source_selection_bias,
        "lookahead_risk": entry.lookahead_risk,
        "source_market": entry.source_market,
        "source_period": entry.source_period,
    }

