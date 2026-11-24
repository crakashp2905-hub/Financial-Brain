from __future__ import annotations

import numpy as np
import pytest
import json
from pathlib import Path

from scripts.research.four_node_grid import main, run_one
from financial_brain.strategies.four_node.backtest import all_variants


def test_grid_contains_all_128_rule_interpretations() -> None:
    assert len(all_variants()) == 128


def test_committed_study_stays_fail_labeled_and_marks_test_period_exploratory() -> None:
    root = Path(__file__).resolve().parents[1]
    artifact = json.loads((root / "docs/strategies/four_node/results/grid.json").read_text())
    assert len(artifact["variants"]) == 128
    assert all(row["DISCOVERY"]["mean_net_bps"] < 0 for row in artifact["variants"])
    assert all(row["VALIDATION"]["mean_net_bps"] < 0 for row in artifact["variants"])
    assert all("TEST" in row for row in artifact["variants"])
    report = (root / "docs/strategies/four_node/results/README.md").read_text(encoding="utf-8")
    assert "not isolated" in report
    assert "not independently rerunnable" in report


def test_grid_refuses_missing_or_empty_data_instead_of_writing_zero_results(tmp_path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(FileNotFoundError, match="does not exist"):
        main(missing, tmp_path / "out", None)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="no minute-bar NPZ inputs"):
        main(empty, tmp_path / "out", None)
    assert not (tmp_path / "out").exists()


def test_grid_inputs_do_not_enable_pickle_execution(tmp_path) -> None:
    source = tmp_path / "object.npz"
    np.savez(source, days=np.array(["2020-01-01"]), o=np.array([object()], dtype=object),
             h=np.array([object()], dtype=object), l=np.array([object()], dtype=object),
             c=np.array([object()], dtype=object))
    with pytest.raises(ValueError, match="Object arrays cannot be loaded"):
        run_one(str(source))
