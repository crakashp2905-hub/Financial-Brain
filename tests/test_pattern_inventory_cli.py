"""Inventory truthfulness, documented commands and CLI behaviour (CP-0004, CP-0355, CP-0364, CP-0218, CP-0393)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from financial_brain.patterns.candles import CANDLE_MAP
from financial_brain.patterns.cli import _records, build_parser, main
from financial_brain.patterns.detectors import DETECTORS
from financial_brain.patterns.inventory import STATUS, counts, inventory

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
CATALOGUE = ROOT / "docs" / "sources" / "pattern-catalogue.v1.json"
MATRIX = ROOT / "docs" / "audits" / "chart-patterns-completion.json"
STATUS_FILE = ROOT / "docs" / "audits" / "pattern-catalogue-status.json"


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONPATH=str(SRC), PATTERN_DEV_MODE="offline")
    return subprocess.run([sys.executable, "-m", "financial_brain.patterns.cli", *args],
                          capture_output=True, text=True, env=env, timeout=120, check=False)


def test_inventory_covers_all_72_catalogue_rows() -> None:
    ids = [row["id"] for row in json.loads(CATALOGUE.read_text(encoding="utf-8"))["patterns"]]
    assert sorted(STATUS) == sorted(ids)
    assert sum(counts().values()) == 72


def test_implemented_rows_name_real_detectors_or_candle_columns() -> None:
    detector_names = {d.__name__ for d in DETECTORS}

    source = (SRC / "financial_brain" / "patterns" / "detectors.py").read_text(encoding="utf-8")
    for entry_id, (status, evidence) in STATUS.items():
        if status != "implemented":
            continue
        if "candles." in evidence:
            column = re.search(r"c_[a-z_]+", evidence)
            assert column and column.group(0) in CANDLE_MAP, f"{entry_id}: {evidence}"
        else:
            func = re.search(r"detectors\.([a-z_]+)", evidence)
            assert func, f"{entry_id}: {evidence}"
            assert f"def {func.group(1)}(" in source, f"{entry_id}: no such detector {func.group(1)}"
    assert detector_names  # sanity: the registry is non-empty


def test_inventory_rows_are_stable_and_unknown_ids_fail() -> None:
    rows = inventory(["N01", "B12"])
    assert [r["id"] for r in rows] == ["N01", "B12"]
    with pytest.raises(KeyError):
        inventory(["N99"])


def test_status_file_matches_inventory() -> None:
    expected = {"rows": inventory(sorted(STATUS)), "counts": counts()}
    assert json.loads(STATUS_FILE.read_text(encoding="utf-8")) == json.loads(json.dumps(expected))


def test_cli_has_no_live_or_order_flags() -> None:
    help_text = build_parser().format_help()
    for forbidden in ("--live", "--order", "--broker", "--submit", "--execute"):
        assert forbidden not in help_text
    sub = build_parser()._subparsers._group_actions[0].choices  # type: ignore[attr-defined]
    assert set(sub) == {"scan", "list", "explain", "paper-demo", "audit"}


def test_documented_commands_run_and_exit_as_documented() -> None:
    assert _cli("scan").returncode == 0
    assert _cli("list", "--limit", "3").returncode == 0
    assert _cli("list", "--state", "BUY").returncode == 1  # invalid enum is rejected
    assert _cli("paper-demo").returncode == 0
    assert _cli("audit", "--matrix", str(MATRIX)).returncode == 0
    assert _cli("audit", "--matrix", str(ROOT / "missing.json")).returncode == 1


def test_explain_known_and_unknown_ids() -> None:
    event_id = next(iter(_records()))
    assert _cli("explain", event_id).returncode == 0
    assert _cli("explain", "pev_nonexistent").returncode == 1


def test_main_returns_nonzero_on_bad_arguments() -> None:
    with pytest.raises(SystemExit):
        main(["not-a-command"])
