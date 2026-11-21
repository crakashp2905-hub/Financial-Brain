"""Paper journal: wizard, frozen manifests, intent states, P&L split, summaries and replay (CP-0266..0280)."""

from __future__ import annotations

import pytest

from financial_brain.patterns.demo import run_pipeline, synthetic_corpus
from financial_brain.patterns.journal import (
    ClosedTrade,
    DailyMark,
    IntentLedger,
    ManifestConflict,
    contribution,
    manifest_identity,
    new_session,
    pnl_split,
    period_summary,
    replay_identity,
    save_manifest,
    scenario_all_loss,
    scenario_no_signal,
)


def _body(**over):
    kw = dict(capital=1_000_000.0, start="2026-01-05", end="2026-03-31",
              strategy_filter=("rectangle", "flag_bullish"), fill_policy="next_session")
    kw.update(over)
    return new_session(**kw)


def test_wizard_validates_inputs_and_runs_without_network() -> None:
    body = _body()
    assert body["network"] is False and body["label"] == "SIMULATED"
    with pytest.raises(ValueError):
        _body(capital=0)
    with pytest.raises(ValueError):
        _body(start="2026-06-01", end="2026-01-01")
    with pytest.raises(ValueError):
        _body(fill_policy="same_bar")


def test_manifest_identity_changes_with_any_field(tmp_path) -> None:
    a = _body()
    b = _body(capital=1_000_001.0)
    assert manifest_identity(a) != manifest_identity(b)
    path = save_manifest(a, tmp_path)
    assert save_manifest(a, tmp_path) == path  # identical re-save is a no-op
    with pytest.raises(ManifestConflict):
        # same identity, different content on disk: refused rather than overwritten
        (tmp_path / path.name).write_text("tampered", encoding="utf-8")
        save_manifest(a, tmp_path)


def test_intent_states_reconcile_with_coverage() -> None:
    ledger = IntentLedger()
    ledger.set_state("i1", "FULLY_FILLED")
    ledger.set_state("i2", "UNFILLED")
    ledger.set_state("i3", "REJECTED")
    cov = ledger.coverage()
    assert cov["total"] == 3 and cov["FULLY_FILLED"] == 1 and cov["UNFILLED"] == 1
    with pytest.raises(ValueError):
        ledger.set_state("i4", "MAYBE")


def test_daily_equity_identity_and_pnl_split() -> None:
    m = DailyMark("2026-01-05", cash=900.0, position_value=200.0, liabilities=0.0)
    assert m.equity == pytest.approx(1100.0)
    closed = [ClosedTrade("A", 100.0, 110.0, 10, 2.0)]
    split = pnl_split(closed, {"B": (50.0, 55.0, 4)})
    assert split["realized"] == pytest.approx(98.0)
    assert split["unrealized"] == pytest.approx(20.0)
    assert split["total"] == pytest.approx(split["realized"] + split["unrealized"])


def test_period_summary_has_drawdown_and_denominator() -> None:
    marks = [DailyMark(f"2026-01-0{i}", cash=100 + x, position_value=0, liabilities=0)
             for i, x in enumerate([0, 20, 5, 30], start=1)]
    summary = period_summary(marks)
    assert summary["days"] == 4
    assert summary["max_drawdown"] == pytest.approx((20 - 5) / 120)
    assert period_summary([])["days"] == 0


def test_pattern_contribution_is_descriptive() -> None:
    out = contribution([("rectangle", 10.0), ("rectangle", -4.0), ("flag_bullish", 2.0)])
    assert out["rectangle"] == {"trades": 2.0, "total": 6.0}


def test_replay_reproduces_identity_and_changes_on_any_difference() -> None:
    body = _body()
    results = {"trades": 3}
    assert replay_identity(body, results) == replay_identity(body, results)
    assert replay_identity(body, results) != replay_identity(body, {"trades": 4})


def test_all_loss_scenario_is_not_hidden() -> None:
    trades = scenario_all_loss(5)
    split = pnl_split(trades, {})
    assert split["realized"] < 0
    assert all(t.realized < 0 for t in trades)


def test_no_signal_scenario_is_zero_not_success() -> None:
    trades, split = scenario_no_signal()
    assert trades == [] and split["total"] == 0.0


def test_end_to_end_synthetic_path_runs_deterministically() -> None:
    corpus = synthetic_corpus()
    first = run_pipeline(corpus)
    second = run_pipeline(corpus)
    assert first == second
    assert len(first["rows"]) > 0
