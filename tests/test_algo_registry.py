"""Source provenance, rights, passive-citation and offline tests (ALG-001..011, ALG-075, ALG-110, ALG-128)."""

from __future__ import annotations

import copy
import json
import re
import socket
from datetime import date
from pathlib import Path

import pytest

from financial_brain.research.external_strategies.registry import (
    RegistryError,
    display_label,
    load_registry,
    registry_hash,
    stale_check,
    validate_entry,
    validate_registry,
    assert_no_edge_label,
)

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "docs" / "sources" / "algorithm-source-registry.json"
RUNTIME_DIRS = [ROOT / "src" / "financial_brain" / "strategies", ROOT / "src" / "financial_brain" / "research" / "external_strategies"]
PINE_OR_MQL_MARKERS = re.compile(r"//@version|#property\s+strict|OnTick\s*\(|MetaTrader|\bta\.(ema|sma|atr)\(|\.mq5\b|\.pine\b")


def _base() -> dict:
    return {
        "id": "TV-999", "title": "t", "author": "a", "platform": "TradingView",
        "url": "https://example.org/x", "access_date": "2026-11-08", "source_type": "strategy",
        "citation": "c", "rights_status": "reference_only",
    }


def test_registry_file_validates_and_has_unique_ids() -> None:
    entries = load_registry(REGISTRY)
    assert len(entries) == 18
    assert len({e.id for e in entries}) == len(entries)


def test_missing_provenance_is_rejected() -> None:
    for key in ("author", "url", "citation", "access_date"):
        bad = _base()
        bad.pop(key)
        with pytest.raises(RegistryError):
            validate_entry(bad)


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(RegistryError, match="duplicate"):
        validate_registry([_base(), _base()])


def test_indicator_cannot_be_an_executable_strategy() -> None:
    bad = _base() | {"source_type": "indicator", "executable": True}
    with pytest.raises(RegistryError, match="indicator"):
        validate_entry(bad)


def test_every_mql5_entry_is_reference_only() -> None:
    for e in load_registry(REGISTRY):
        if e.platform.startswith("MQL5"):
            assert e.rights_status == "reference_only"


def test_tradingview_entries_never_imply_blanket_licence() -> None:
    for e in load_registry(REGISTRY):
        assert e.rights_status == "reference_only"


def test_licence_verified_needs_a_recorded_reference() -> None:
    bad = _base() | {"rights_status": "licence_verified"}
    with pytest.raises(RegistryError, match="licence_reference"):
        validate_entry(bad)
    assert validate_entry(bad | {"licence_reference": "LIC-1"}).rights_status == "licence_verified"


def test_runtime_modules_contain_no_pine_or_mql5_source_markers() -> None:
    for folder in RUNTIME_DIRS:
        for path in folder.glob("*.py"):
            assert not PINE_OR_MQL_MARKERS.search(path.read_text(encoding="utf-8")), path.name


def test_vendor_figures_render_only_as_source_claims() -> None:
    entry = load_registry(REGISTRY)[0]
    assert display_label(entry, claimed_return=True) == "SOURCE_CLAIM"
    with pytest.raises(RegistryError):
        assert_no_edge_label("EDGE_CONFIRMED")


def test_registry_hash_changes_when_a_snapshot_field_changes() -> None:
    entries = load_registry(REGISTRY)
    changed = copy.deepcopy(json.loads(REGISTRY.read_text(encoding="utf-8")))
    changed["entries"][0]["access_date"] = "2026-11-09"
    assert registry_hash(entries) != registry_hash(validate_registry(changed["entries"]))
    assert registry_hash(entries) == registry_hash(load_registry(REGISTRY))


def test_families_and_market_tags_are_recorded() -> None:
    entries = load_registry(REGISTRY)
    assert all(e.family and e.family != "unclassified" for e in entries)
    assert all(e.paper_only for e in entries)


def test_stale_source_is_flagged() -> None:
    entry = load_registry(REGISTRY)[0]
    assert not stale_check(entry, as_of=date(2026, 11, 10), max_age_days=30)
    assert stale_check(entry, as_of=date(2027, 6, 1), max_age_days=30)


def test_registry_and_strategies_work_with_the_network_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*_a: object, **_k: object) -> None:
        raise RuntimeError("network is disabled in tests")

    monkeypatch.setattr(socket, "socket", blocked)
    assert len(load_registry(REGISTRY)) == 18
