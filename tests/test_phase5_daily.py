import re
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from financial_brain.opportunity import engine as E
from financial_brain.paper import daily


def _cs(n=200):
    rng = np.random.default_rng(1)
    return pd.DataFrame({
        "lineage": [f"L{i}" for i in range(n)], "isin": "I", "ticker": [f"T{i}" for i in range(n)],
        "price": 100.0, "adv20": np.exp(rng.normal(np.log(2e8), 1, n)),
        "vol_60": rng.uniform(.15, .6, n), "mom_12_1": rng.normal(0, .3, n),
        "dist_52w_high": -rng.uniform(0, .5, n), "ret_20d": rng.normal(0, .08, n)})


def test_daily_loop_records_decisions_and_reports_an_empty_book(monkeypatch):
    seen = {}
    monkeypatch.setattr(E, "scan", lambda con, d, cfg: E.decide(_cs(), cfg=cfg))
    monkeypatch.setattr(daily.M, "record", lambda con, **kw: seen.update(kw) or len(kw["rows"]))
    monkeypatch.setattr(daily.M, "resolve", lambda con, **kw: {"resolved": 0})
    monkeypatch.setattr(daily.M, "report", lambda con, **kw: {})
    r = daily.run_daily(None, as_of=date(2026, 9, 18))
    assert seen["run_id"] == daily.LIVE_RUN and len(seen["rows"]) == 200
    assert r["nothing_clears"] and r["paper_book"] == []


def test_daily_module_cannot_place_an_order():
    src = Path(daily.__file__).read_text(encoding="utf-8").lower()
    code = re.sub(r'""".*?"""', "", src, flags=re.S)
    for word in ("place_order", "/orders", "kiteconnect", "requests", "urlopen", "execution_enabled"):
        assert word not in code


def test_first_live_run_has_an_empty_scorecard_not_a_crash(monkeypatch):
    monkeypatch.setattr(E, "scan", lambda con, d, cfg: E.decide(_cs(), cfg=cfg))
    monkeypatch.setattr(daily.M, "record", lambda con, **kw: len(kw["rows"]))
    monkeypatch.setattr(daily.M, "resolve", lambda con, **kw: {"resolved": 0})

    def refuse(con, **kw):
        raise daily.M.MemoryError_("no resolved rows")
    monkeypatch.setattr(daily.M, "report", refuse)
    r = daily.run_daily(None, as_of=date(2026, 9, 18))
    assert r["scorecard"]["resolved_decisions"] == 0
