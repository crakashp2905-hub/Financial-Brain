"""Tracker integrity: all 128 addendum IDs present once, valid statuses, evidence files exist, bar is correct (ALG-117, ALG-121, ALG-124)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _tracker():
    spec = importlib.util.spec_from_file_location("algo_progress", ROOT / "scripts" / "algo_progress.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_all_128_ids_are_present_once() -> None:
    mod = _tracker()
    ids = [r[0] for r in mod.ROWS]
    assert len(ids) == 128 and len(set(ids)) == 128
    assert ids[0] == "ALG-001" and ids[-1] == "ALG-128"


def test_statuses_are_valid_and_evidence_files_exist() -> None:
    mod = _tracker()
    for task_id, _wave, status, tests, _note in mod.ROWS:
        assert status in mod.VALID, task_id
        for t in tests:
            assert (ROOT / t).exists(), (task_id, t)


def test_progress_bar_is_exact() -> None:
    mod = _tracker()
    assert mod.progress_bar(64, 128, width=10) == "[#####-----] 64/128 (50.0%)"
    assert mod.progress_bar(0, 0, width=4) == "[----] 0/0 (0.0%)"


def test_committed_status_file_matches_the_tracker() -> None:
    mod = _tracker()
    status = mod.build()
    committed = (ROOT / "docs" / "audits" / "tradingview-mql5-integration-status.json").read_text(encoding="utf-8")
    import json

    assert json.loads(committed) == json.loads(__import__("json").dumps(status))
