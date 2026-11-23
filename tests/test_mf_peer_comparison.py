from datetime import date, timedelta

from financial_brain.mf_peer_comparison import compare_same_category


def _returns(scale: float, count: int = 40):
    start = date(2026, 1, 1)
    return [(start + timedelta(days=i), scale * (0.001 if i % 2 else -0.0005))
            for i in range(count)]


def test_small_cap_fund_cannot_be_ranked_against_overnight_debt() -> None:
    result = compare_same_category(
        left_category="Equity Scheme - Small Cap Fund",
        right_category="Debt Scheme - Overnight Fund",
        left_returns=_returns(1), right_returns=_returns(1),
        left_benchmark_return_type="TRI", right_benchmark_return_type="TRI",
    )
    assert result.status == "REFUSED"
    assert result.reasons == ("CROSS_CATEGORY_COMPARISON_BLOCKED",)
    assert result.left_equal_risk_return is None


def test_same_category_returns_are_compared_at_no_more_than_lower_observed_risk() -> None:
    result = compare_same_category(
        left_category="Equity Scheme - Small Cap Fund",
        right_category="equity scheme - small cap fund",
        left_returns=_returns(1), right_returns=_returns(2),
        left_benchmark_return_type="TRI", right_benchmark_return_type="PRICE",
        left_ter_pct=0.65, right_ter_pct=1.2,
    )
    assert result.status == "COMPARABLE"
    assert result.common_observations == 40
    assert result.target_annualized_volatility == result.left_annualized_volatility
    assert result.right_annualized_volatility > result.target_annualized_volatility
    assert result.left_benchmark_is_tri and not result.right_benchmark_is_tri
    assert result.reasons == ("BENCHMARK_NOT_TRI",)
    assert (result.left_ter_pct, result.right_ter_pct) == (0.65, 1.2)


def test_thin_overlap_is_not_ranked() -> None:
    result = compare_same_category(
        left_category="Equity Scheme - Large Cap Fund",
        right_category="Equity Scheme - Large Cap Fund",
        left_returns=_returns(1, 10), right_returns=_returns(1, 10),
        left_benchmark_return_type="TRI", right_benchmark_return_type="TRI",
    )
    assert result.status == "INSUFFICIENT_DATA"
    assert result.left_equal_risk_return is None
