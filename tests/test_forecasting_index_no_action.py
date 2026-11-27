import pytest

from financial_brain.forecasting.index_no_action import NoActionError, compare_to_no_action


def test_cash_no_action_comparison_uses_after_cost_book_returns():
    rows = [
        {"session": "2026-11-24", "gross_return_on_book": .02,
         "all_in_cost_return_on_book": .01, "cash_return_on_book": .001},
        {"session": "2026-11-25", "gross_return_on_book": 0.0,
         "all_in_cost_return_on_book": 0.0, "cash_return_on_book": .001},
        {"session": "2026-11-26", "gross_return_on_book": -.01,
         "all_in_cost_return_on_book": .005, "cash_return_on_book": .001},
    ]
    report = compare_to_no_action(rows)
    assert report["baseline"] == "hold_cash_avoid_options"
    assert report["sessions"] == 3
    assert report["all_in_cost_return_sum"] == pytest.approx(.015)
    assert report["option_net_cumulative_return"] == pytest.approx((1.01 * 1.0 * .985) - 1)
    assert report["cash_no_action_cumulative_return"] == pytest.approx((1.001 ** 3) - 1)
    assert report["net_excess_over_cash"] < 0
    assert report["directional_accuracy_used_as_economic_evidence"] is False


def test_no_action_comparison_rejects_missing_or_invalid_session_economics():
    with pytest.raises(NoActionError, match="required"):
        compare_to_no_action([])
    with pytest.raises(NoActionError, match="unique and chronological"):
        compare_to_no_action([{"session": "2026-11-25", "gross_return_on_book": 0,
                               "all_in_cost_return_on_book": 0, "cash_return_on_book": 0},
                              {"session": "2026-11-24", "gross_return_on_book": 0,
                               "all_in_cost_return_on_book": 0, "cash_return_on_book": 0}])
    with pytest.raises(NoActionError, match="finite fractions"):
        compare_to_no_action([{"session": "2026-11-25", "gross_return_on_book": .1,
                               "all_in_cost_return_on_book": float("nan"),
                               "cash_return_on_book": 0}])
