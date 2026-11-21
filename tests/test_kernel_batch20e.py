from __future__ import annotations

import json

import pytest

from financial_brain.kernel.batch20e_explain import (
    export_transcript,
    explain_decision,
    explain_rejection,
    explain_signal,
    explain_value,
    hypothesis_check,
    retrieve_context,
)
from financial_brain.kernel.batch20e_strategies import (
    bootstrap_paths,
    covered_call_profit,
    dividend_reinvest_units,
    fx_session_reversion,
    generalises,
    gap_fade,
    hedge_notional,
    orb_signal,
    rectangle_breakout,
    scenario_regression,
    select_top_fraction,
    selection_bias_adjust,
    vertical_limits,
    vwap_reversion,
)


def test_factor_baseline_picks_lowest_volatility_half() -> None:
    assert select_top_fraction({"A": 0.3, "B": 0.1, "C": 0.2, "D": 0.4}, 0.5, ascending=True) == ["B", "C"]
    assert select_top_fraction({"A": 1.0, "B": 1.0}, 0.5, ascending=False) == ["A"]


def test_gap_fade_needs_a_real_gap_that_reverses() -> None:
    assert gap_fade(100.0, 102.0, 101.0) == "SHORT"
    assert gap_fade(100.0, 98.0, 99.0) == "LONG"
    assert gap_fade(100.0, 100.5, 101.0) == "NONE"


def test_opening_range_breakout_directions() -> None:
    assert orb_signal(105.0, 104.0, 100.0) == 1
    assert orb_signal(99.0, 104.0, 100.0) == -1
    assert orb_signal(102.0, 104.0, 100.0) == 0


def test_vwap_reversion_is_filtered_by_volume() -> None:
    assert vwap_reversion(102.0, 100.0, 0.01, volume_ratio=1.0, max_volume_ratio=1.5) == -1
    assert vwap_reversion(102.0, 100.0, 0.01, volume_ratio=2.0, max_volume_ratio=1.5) == 0


def test_rectangle_breakout_needs_confirmation() -> None:
    assert rectangle_breakout(105.0, 104.0, confirmed=True) == 1
    assert rectangle_breakout(105.0, 104.0, confirmed=False) == 0


def test_index_hedge_offsets_beta_weighted_exposure() -> None:
    assert hedge_notional(1000.0, 1.2) == pytest.approx(-1200.0)


def test_covered_call_profit_is_capped_above_strike() -> None:
    assert covered_call_profit(100.0, 110.0, 2.0, expiry_spot=120.0, shares=10) == pytest.approx(120.0)
    assert covered_call_profit(100.0, 110.0, 2.0, expiry_spot=90.0, shares=10) == pytest.approx(-80.0)


def test_vertical_limits_are_paper_only_with_bounded_loss() -> None:
    limits = vertical_limits(100.0, 110.0, 3.0)
    assert limits == {"max_loss": 3.0, "max_profit": 7.0, "label": "PAPER_ONLY"}
    with pytest.raises(ValueError):
        vertical_limits(110.0, 100.0, 3.0)


def test_fx_session_reversion_fades_moves_beyond_deviation() -> None:
    assert fx_session_reversion(1.01, 1.0, 0.005) == -1
    assert fx_session_reversion(1.0, 1.0, 0.005) == 0


def test_dividend_reinvestment_buys_whole_units() -> None:
    assert dividend_reinvest_units(100.0, 50.0, 1.0) == 2


def test_scenario_regression_flags_sign_mismatches() -> None:
    assert scenario_regression({"a": -5.0, "b": 3.0}, {"a": 1, "b": 1}) == ["a"]
    with pytest.raises(KeyError):
        scenario_regression({}, {"a": 1})


def test_bootstrap_is_seeded_and_returns_one_result_per_path() -> None:
    a = bootstrap_paths([0.01, -0.02, 0.03], n_paths=5, horizon=4, seed=11)
    assert len(a) == 5
    assert a == bootstrap_paths([0.01, -0.02, 0.03], n_paths=5, horizon=4, seed=11)


def test_selection_bias_penalty_grows_with_trials_and_is_labelled() -> None:
    one = selection_bias_adjust(1.0, 1)
    many = selection_bias_adjust(1.0, 100)
    assert one["adjusted"] == pytest.approx(1.0)
    assert many["adjusted"] == pytest.approx(1.0 - 0.1 * 4.605170, rel=1e-4)
    assert many["label"] == "ILLUSTRATIVE"


def test_context_retrieval_returns_only_asked_keys() -> None:
    assert retrieve_context({"a": 1, "b": 2}, ["a"]) == {"a": 1}
    with pytest.raises(KeyError):
        retrieve_context({"a": 1}, ["z"])


def test_numbers_are_shown_with_their_calculation() -> None:
    assert explain_value("return", 0.05, "105 / 100 - 1") == "return = 0.05 (105 / 100 - 1)"
    with pytest.raises(ValueError):
        explain_value("return", 0.05, " ")


def test_duplicate_hypotheses_are_flagged_against_the_inventory() -> None:
    assert hypothesis_check("12m momentum", {"12m momentum"}) == "DUPLICATE_OF_PRIOR_TRIAL"
    assert hypothesis_check("gap fade", {"12m momentum"}) == "NEW"


def test_only_registered_signals_are_explained() -> None:
    assert "registered" in explain_signal("orb", {"orb"})
    with pytest.raises(KeyError):
        explain_signal("secret_alpha", {"orb"})


def test_decision_explanation_covers_every_alternative() -> None:
    record = explain_decision("NO_TRADE", "no edge after costs", {"BUY": "spread too wide", "SHORT": "no borrow"})
    assert record["alternatives"] == {"BUY": "spread too wide", "SHORT": "no borrow"}
    with pytest.raises(ValueError, match="alternative BUY"):
        explain_decision("NO_TRADE", "no edge", {"BUY": " "})


def test_rejections_use_known_codes_only() -> None:
    assert "NO_MARGIN" in explain_rejection("NO_MARGIN", "need 500, have 300")
    with pytest.raises(ValueError, match="unknown rejection"):
        explain_rejection("MYSTERY", "x")


def test_transcript_export_is_json_lines_and_refuses_empty_messages() -> None:
    text = export_transcript([{"role": "user", "text": "hi"}, {"role": "agent", "text": "hello"}])
    lines = text.splitlines()
    assert [json.loads(line)["role"] for line in lines] == ["user", "agent"]
    with pytest.raises(ValueError, match="role and text"):
        export_transcript([{"role": "user", "text": ""}])


def test_candidate_must_generalise_across_assets() -> None:
    assert generalises({"A": 0.1, "B": 0.2, "C": -0.1}, min_positive_share=0.6) is True
    assert generalises({"A": 0.1, "B": -0.2, "C": -0.1}, min_positive_share=0.6) is False
    with pytest.raises(ValueError):
        generalises({}, 0.5)
