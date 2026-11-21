"""Wave 7: catalogue metadata, schema sync, report empty state and styling, preview-all, confinement (CP-0013, 0015, 0017, 0022, 0026, 0249, 0254, 0263, 0268, 0328)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from financial_brain.patterns.catalogue import (
    REQUIRED_KEYS,
    MissingParameters,
    load_catalogue,
    require_parameters,
)
from financial_brain.patterns.demo import preview_all, synthetic_corpus
from financial_brain.patterns.report import InvalidOverlay, overlay_style, render_html

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
CATALOGUE = ROOT / "docs" / "sources" / "pattern-catalogue.v1.json"
SCHEMA = ROOT / "docs" / "schemas" / "pattern-catalogue.v1.schema.json"
REFERENCE = ROOT / "docs" / "audits" / "pattern-reference-index.json"


def test_schema_required_keys_match_the_validator() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert set(schema["required"]) == set(REQUIRED_KEYS)
    assert schema["properties"]["source"]["enum"] == ["nison", "bulkowski"]


def test_every_entry_is_conditional_historical_with_unverified_reference() -> None:
    report = load_catalogue(CATALOGUE)
    assert report.ok
    for entry in report.entries:
        assert entry.reference["page_unverified"] is True
        assert entry.reference["edition"] is None
        if entry.stats:
            assert entry.claim_kind == "historical_conditional"


def test_thresholds_are_unset_and_undefined_parameters_refuse() -> None:
    entry = load_catalogue(CATALOGUE).entries[0]
    assert entry.parameters is None and entry.knowledge_lag is None
    with pytest.raises(MissingParameters):
        require_parameters(entry)


def test_reference_index_is_in_sync_with_the_inventory() -> None:
    index = json.loads(REFERENCE.read_text(encoding="utf-8"))["entries"]
    catalogue_ids = {e.id for e in load_catalogue(CATALOGUE).entries}
    assert set(index) == catalogue_ids


def test_empty_result_says_it_is_not_a_signal() -> None:
    page = render_html([], title="empty")
    assert "not a signal" in page


def test_overlay_states_are_visually_distinct_and_unknown_states_refused() -> None:
    styles = {state: overlay_style(state)["dash"] for state in ("CONFIRMED", "TENTATIVE", "INVALIDATED")}
    assert len(set(styles.values())) == 3
    with pytest.raises(InvalidOverlay):
        overlay_style("GUESSED")


def test_report_snapshot_is_unchanged() -> None:
    rows = [{"symbol": "SYN01", "detector": "rectangle", "state": "CONFIRMED", "geometry_fit": 1.0,
             "data_quality": 1.0, "research_evidence": "DESCRIPTIVE", "calibration": "not calibrated", "reasons": ""}]
    page = render_html(rows, title="snapshot")
    expected = (ROOT / "docs" / "audits" / "pattern-report-snapshot.sha256").read_text(encoding="utf-8").strip()
    import hashlib

    assert hashlib.sha256(page.encode("utf-8")).hexdigest() == expected


def test_preview_all_labels_every_fill_assumed() -> None:
    previews = preview_all(synthetic_corpus())
    assert previews
    for p in previews:
        assert p["entry_index"] == p["signal_index"] + 1
        assert p["fill_label"] == "ASSUMED"
        assert p["label"] == "SYNTHETIC"


def test_demo_writes_only_inside_its_output_folder(tmp_path: Path) -> None:
    env = dict(os.environ, PYTHONPATH=str(SRC), PATTERN_DEV_MODE="offline")
    before = {p for p in tmp_path.rglob("*")}
    out = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, "-m", "financial_brain.patterns.demo", "--offline", "--out", str(out)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    created = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p not in before}
    assert created == {"out", "out/report.html", "out/export.json"}
