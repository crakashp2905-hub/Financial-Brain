"""ADR-0006: the small tier's fixed checks, on a planted registered series."""
import json

import numpy as np
import pandas as pd

from financial_brain.evaluation import oos_monitor as OM
from financial_brain.evaluation import small_tier as ST


def _setup(tmp_path, monkeypatch, mean, g4_pass=True):
    rng = np.random.default_rng(0)
    idx = pd.date_range("2016-01-31", "2026-06-30", freq="ME")
    s = pd.Series(mean + 0.02 * rng.standard_normal(len(idx)), index=idx)
    path = tmp_path / "bt.parquet"
    pd.DataFrame({"net": s}).to_parquet(path)
    monkeypatch.setattr(OM, "REGISTERED", (OM.Registration("strat", "p", "2026-10-12", 21, str(path)),))
    monkeypatch.setitem(ST.LEDGER_NAME, "strat", "T")
    led = tmp_path / "ledger.jsonl"
    led.write_text(json.dumps({"kind": "trial", "name": "T", "date": "2026-10-13",
                               "g4_stat": 3.0, "g4_null_q95": 1.0 if g4_pass else 5.0}) + "\n")
    return led


def test_strong_backtest_without_a_live_record_does_not_qualify(tmp_path, monkeypatch):
    led = _setup(tmp_path, monkeypatch, 0.02)
    r = ST.assess("strat", None, ledger_path=led)
    assert not r["qualifies"] and r["why"] == [f"live paper >= {ST.MIN_LIVE_SESSIONS} sessions",
                                               "never below the band at a month end"]


def test_weak_backtest_fails_the_full_sample_bar(tmp_path, monkeypatch):
    led = _setup(tmp_path, monkeypatch, 0.002)
    r = ST.assess("strat", None, ledger_path=led)
    assert f"full-sample t >= {ST.MIN_FULL_T}" in r["why"]
