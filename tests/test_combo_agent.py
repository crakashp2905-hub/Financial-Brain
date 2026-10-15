"""Combination agent mechanics (no data, no model): dedupe, statistics, the prior's mapping, the locked validation."""
import numpy as np
import pandas as pd
import pytest

from financial_brain.research import combo_agent as CA


def test_dedupe_keeps_one_event_per_name_per_five_sessions():
    e = pd.DataFrame({"sym": ["A"] * 4 + ["B"], "pos": [10, 12, 15, 30, 11]})
    assert list(CA._dedupe(e)["pos"]) == [10, 15, 30, 11]


def test_stats_apply_side_and_cost():
    d = pd.date_range("2018-01-01", periods=90, freq="7D")
    e = pd.DataFrame({"d": d, "x5": 0.01})
    long, short = CA._stats(e, 1, 5), CA._stats(e, -1, 5)
    assert abs(long["mean"] - (0.01 - CA.COST)) < 1e-12 and abs(short["mean"] - (-0.01 - CA.COST)) < 1e-12


def test_prior_maps_choices_and_drops_no_mechanism():
    pr = pd.DataFrame({"trigger": ["a:up", "a:up"], "context": ["none", "nifty_up"],
                       "choice": [CA.CHOICES[1], CA.CHOICES[-1]], "confidence": [0.6, 0.7]})
    assert CA.combos_from_prior(pr, ["a:up"]) == [("a:up", "none", 1, 5)]
    assert len(CA.combos_from_prior(None, ["a:up"])) == len(CA.CONTEXTS) * 2 * len(CA.HORIZONS)


def test_validation_is_locked_without_a_trial(monkeypatch):
    from financial_brain.evaluation import standard as S
    monkeypatch.setattr(S, "check_budget", lambda *a, **k: {"ok": False, "quarter": "2026Q4", "used": 11, "budget": 11})
    with pytest.raises(PermissionError, match="locked"):
        CA.validate()
