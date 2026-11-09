"""Catalogue validation tests (CP-0013..CP-0025) against the real 72-entry source."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from financial_brain.patterns.catalogue import (
    DIRECTION_CLASSES,
    FAMILIES,
    load_catalogue,
    main,
    validate_catalogue,
)

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = ROOT / "docs" / "sources" / "pattern-catalogue.v1.json"


@pytest.fixture(scope="module")
def raw() -> dict:
    return json.loads(CATALOGUE.read_text(encoding="utf-8"))


def test_real_catalogue_validates_to_72_entries(raw: dict) -> None:
    report = validate_catalogue(raw, expected_count=72)
    assert report.errors == []
    assert len(report.entries) == 72
    assert report.counts_by_source() == {"bulkowski": 47, "nison": 25}


def test_families_and_directions_stay_in_controlled_enumerations(raw: dict) -> None:
    report = validate_catalogue(raw)
    assert {e.family for e in report.entries} <= FAMILIES
    assert {e.direction_class for e in report.entries} <= DIRECTION_CLASSES
    assert all(e.definition_status == "descriptive" for e in report.entries)


def test_unknown_source_family_is_classified_unclassified_not_guessed(raw: dict) -> None:
    report = validate_catalogue(raw)
    by_id = {e.id: e for e in report.entries}
    assert by_id["B23"].family == "unclassified"
    assert by_id["B23"].family_raw == "bearish"
    assert by_id["N01"].family == "candle"


def test_book_stats_parse_only_plain_numbers(raw: dict) -> None:
    report = validate_catalogue(raw)
    by_id = {e.id: e for e in report.entries}
    # Pure integer sample size is typed; compound text is kept verbatim with value None.
    n_stat = by_id["B01"].stats["n"]
    assert n_stat.value == 77 and n_stat.source_claim_text == "77"
    failure = by_id["B02"].stats["failure_pct"]
    assert failure.value is None
    assert failure.source_claim_text is not None
    assert by_id["B02"].sample_total == by_id["B02"].stats["n"].value


def test_duplicate_id_fails_closed(raw: dict) -> None:
    bad = copy.deepcopy(raw)
    bad["patterns"][1]["id"] = bad["patterns"][0]["id"]
    report = validate_catalogue(bad)
    assert any("duplicate id" in message for message in report.errors)


def test_prefix_must_match_source(raw: dict) -> None:
    bad = copy.deepcopy(raw)
    bad["patterns"][0]["source"] = "bulkowski"
    report = validate_catalogue(bad)
    assert any("prefix does not match" in message for message in report.errors)


def test_missing_key_and_unknown_stat_key_are_rejected(raw: dict) -> None:
    bad = copy.deepcopy(raw)
    del bad["patterns"][0]["caveat"]
    stats_row = next(row for row in bad["patterns"] if isinstance(row["book_stats"], dict))
    stats_row["book_stats"]["mystery"] = "1"
    report = validate_catalogue(bad)
    assert any("missing keys" in message and "caveat" in message for message in report.errors)
    assert any("unknown book_stats keys" in message for message in report.errors)


def test_count_mismatch_is_reported(raw: dict) -> None:
    report = validate_catalogue(raw, expected_count=73)
    assert any("expected 73" in message for message in report.errors)


def test_malformed_top_level_fails_closed() -> None:
    assert not validate_catalogue({"patterns": "nope"}).ok
    assert not validate_catalogue({}).ok


def test_cli_exit_codes(tmp_path: Path) -> None:
    assert main([str(CATALOGUE), "--expect", "72"]) == 0
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"patterns": [{"id": "X9"}]}), encoding="utf-8")
    assert main([str(broken)]) == 1
    assert main([str(tmp_path / "missing.json")]) == 1


def test_load_catalogue_is_read_only(raw: dict) -> None:
    before = CATALOGUE.read_bytes()
    load_catalogue(CATALOGUE)
    assert CATALOGUE.read_bytes() == before
