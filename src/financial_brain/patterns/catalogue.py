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
from dataclasses import dataclass, field
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


if __name__ == "__main__":
    raise SystemExit(main())
