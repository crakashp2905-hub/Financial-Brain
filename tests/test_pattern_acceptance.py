"""Acceptance tests for the offline trader workflows (stages 22, 24-26: CP-0339..0392 subset)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from financial_brain.patterns.demo import (
    NOT_EDGE,
    backup,
    corpus_hash,
    health,
    paper_preview,
    restore,
    run_pipeline,
    signoff,
    synthetic_corpus,
)
from financial_brain.patterns.detectors import Bars, InvalidBars, discover
from financial_brain.patterns.report import render_html

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
COMPLETION = ROOT / "docs" / "audits" / "chart-patterns-completion.json"
PROTECTED = ROOT / "docs" / "audits" / "protected-baseline.json"


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONPATH=str(SRC), PATTERN_DEV_MODE="offline")
    return subprocess.run(
        [sys.executable, "-m", "financial_brain.patterns.demo", *args],
        capture_output=True, text=True, env=env, timeout=120, check=False,
    )


def test_offline_demo_runs_with_networking_disabled_and_is_labelled() -> None:
    proc = _run_cli("--offline")
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout)
    assert summary["label"] == "SYNTHETIC"
    assert summary["status"] == NOT_EDGE
    assert summary["paper_preview"]["fill_label"] == "ASSUMED"


def test_demo_refuses_to_run_without_offline_flag() -> None:
    proc = _run_cli()
    assert proc.returncode == 2


def test_demo_output_is_reproducible() -> None:
    a = _run_cli("--offline")
    b = _run_cli("--offline")
    assert json.loads(a.stdout) == json.loads(b.stdout)


def test_watchlist_discovery_matches_direct_run() -> None:
    corpus = synthetic_corpus()
    result = run_pipeline(corpus)
    direct = sum(len(discover(bars)) for bars in corpus.values())
    assert result["formation_count"] == direct > 0
    assert len(result["rows"]) == direct


def test_paper_preview_never_fills_on_the_signal_bar() -> None:
    preview = paper_preview(synthetic_corpus())
    assert preview is not None
    assert preview["entry_index"] > 0
    assert preview["fill_label"] == "ASSUMED"


def test_empty_data_mode_does_not_crash_and_says_so() -> None:
    result = run_pipeline({})
    assert result["rows"] == [] and result["formation_count"] == 0


def test_hostile_ticker_is_escaped_in_html_report() -> None:
    rows = [{"symbol": '"><img src=x onerror=alert(1)>', "detector": "rectangle", "state": "CANDIDATE"}]
    page = render_html(rows, title="t")
    assert "<img" not in page
    assert "&lt;img" in page


def test_backup_restore_round_trip_and_checksum_guard() -> None:
    events = [{"event_id": "pev_1", "status": "CANDIDATE"}, {"event_id": "pev_2", "status": "FAILED"}]
    text, checksum = backup(events)
    assert restore(text, checksum) == events
    with pytest.raises(ValueError, match="checksum"):
        restore(text.replace("pev_1", "pev_9"), checksum)


def test_health_check_reports_offline_and_catalogue() -> None:
    report = health(ROOT / "docs" / "sources" / "pattern-catalogue.v1.json", None)
    assert report["checks"]["catalogue_present"] is True
    assert report["checks"]["offline_mode"] is True


def test_dual_rating_never_claims_market_edge() -> None:
    report = signoff(COMPLETION)
    assert report["PROVEN_MARKET_EDGE"].startswith("UNKNOWN")
    assert report["status"] == NOT_EDGE
    assert report["engineering"] in ("ENGINEERING_IN_PROGRESS", "ENGINEERING_TRADER_READY")


def test_protected_records_are_byte_unchanged() -> None:
    baseline = json.loads(PROTECTED.read_text(encoding="utf-8"))["files"]
    for rel, expected in baseline.items():
        actual = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
        assert actual == expected, f"protected record changed: {rel}"


def test_corpus_hash_is_pinned_across_runs() -> None:
    assert corpus_hash(synthetic_corpus()) == corpus_hash(synthetic_corpus())


def test_fixture_length_guard() -> None:
    with pytest.raises(InvalidBars):
        Bars((1.0,), (1.0,), (1.0,), (1.0, 2.0))


def test_docs_make_no_unqualified_performance_claims() -> None:
    banned = ("guaranteed profit", "zero-loss trading", "proven profitable", "10/10 real-money")
    for doc in sorted((ROOT / "docs").glob("PATTERNS-*.md")):
        text = doc.read_text(encoding="utf-8").lower()
        for phrase in banned:
            if phrase in text:
                # allowed only inside an explicit prohibition sentence
                for line in text.splitlines():
                    if phrase in line:
                        assert any(neg in line for neg in ("not ", "no ", "never", "without", "forbid", "prohibit", "refus", "cannot", "will not")), (
                            f"{doc.name}: unqualified claim '{phrase}'"
                        )


def test_corpus_hash_matches_pinned_value() -> None:
    pinned = (ROOT / "docs" / "audits" / "demo-corpus.sha256").read_text(encoding="utf-8").strip()
    assert corpus_hash(synthetic_corpus()) == pinned

