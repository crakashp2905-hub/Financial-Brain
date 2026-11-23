from datetime import date, datetime, timezone
from decimal import Decimal
import pytest
from financial_brain.research.mf_peer_comparison import MfPeerComparisonError, MfPeerPeriod, compare_same_category_equal_risk
from datetime import timedelta
from financial_brain.mf_peer_comparison import compare_same_category

def _period(scheme: str, category: str, ret: str, vol: str) -> MfPeerPeriod:
    return MfPeerPeriod(scheme_code=scheme, category_id=category, benchmark_tri='Nifty 100 TRI', benchmark_effective_from=date(2025, 1, 1), benchmark_effective_to=None, period_start=date(2025, 1, 1), period_end=date(2025, 12, 31), total_return_percent=Decimal(ret), annualized_volatility_percent=Decimal(vol), source_sha256='e' * 64, known_at=datetime(2026, 1, 2, tzinfo=timezone.utc))

def test_same_category_returns_are_scaled_to_a_common_risk_budget():
    peers = compare_same_category_equal_risk((_period('A', 'EQUITY_SMALL_CAP', '20', '30'), _period('B', 'EQUITY_SMALL_CAP', '12', '15')), risk_budget_percent=Decimal('10'), risk_free_return_percent=Decimal('5'))
    by_scheme = {peer.scheme_code: peer for peer in peers}
    assert by_scheme['A'].risk_normalized_return_percent == Decimal('10')
    assert by_scheme['B'].risk_normalized_return_percent == Decimal('9.666666666666666666666666667')
    assert peers[0].scheme_code == 'A'

def test_cross_category_comparison_is_rejected():
    with pytest.raises(MfPeerComparisonError, match='cross-category'):
        compare_same_category_equal_risk((_period('A', 'EQUITY_SMALL_CAP', '20', '30'), _period('B', 'DEBT_OVERNIGHT', '7', '2')), risk_budget_percent=Decimal('1'), risk_free_return_percent=Decimal('5'))

def test_comparison_requires_aligned_windows_and_no_leverage():
    first = _period('A', 'EQUITY_SMALL_CAP', '20', '30')
    second = _period('B', 'EQUITY_SMALL_CAP', '12', '15')
    with pytest.raises(MfPeerComparisonError, match='cannot scale.*leverage'):
        compare_same_category_equal_risk((first, second), risk_budget_percent=Decimal('16'), risk_free_return_percent=Decimal('5'))
    changed = MfPeerPeriod(**{**second.__dict__, 'period_end': date(2025, 12, 30)})
    with pytest.raises(MfPeerComparisonError, match='same period'):
        compare_same_category_equal_risk((first, changed), risk_budget_percent=Decimal('10'), risk_free_return_percent=Decimal('5'))

def test_benchmark_version_must_cover_the_complete_period():
    with pytest.raises(MfPeerComparisonError, match='cover the full period'):
        MfPeerPeriod(**{**_period('A', 'EQUITY_SMALL_CAP', '20', '30').__dict__, 'benchmark_effective_to': date(2025, 6, 30)})

def _returns(scale: float, count: int=40):
    start = date(2026, 1, 1)
    return [(start + timedelta(days=i), scale * (0.001 if i % 2 else -0.0005)) for i in range(count)]

def test_small_cap_fund_cannot_be_ranked_against_overnight_debt() -> None:
    result = compare_same_category(left_category='Equity Scheme - Small Cap Fund', right_category='Debt Scheme - Overnight Fund', left_returns=_returns(1), right_returns=_returns(1), left_benchmark_return_type='TRI', right_benchmark_return_type='TRI')
    assert result.status == 'REFUSED'
    assert result.reasons == ('CROSS_CATEGORY_COMPARISON_BLOCKED',)
    assert result.left_equal_risk_return is None

def test_same_category_returns_are_compared_at_no_more_than_lower_observed_risk() -> None:
    result = compare_same_category(left_category='Equity Scheme - Small Cap Fund', right_category='equity scheme - small cap fund', left_returns=_returns(1), right_returns=_returns(2), left_benchmark_return_type='TRI', right_benchmark_return_type='PRICE', left_ter_pct=0.65, right_ter_pct=1.2)
    assert result.status == 'COMPARABLE'
    assert result.common_observations == 40
    assert result.target_annualized_volatility == result.left_annualized_volatility
    assert result.right_annualized_volatility > result.target_annualized_volatility
    assert result.left_benchmark_is_tri and (not result.right_benchmark_is_tri)
    assert result.reasons == ('BENCHMARK_NOT_TRI',)
    assert (result.left_ter_pct, result.right_ter_pct) == (0.65, 1.2)

def test_thin_overlap_is_not_ranked() -> None:
    result = compare_same_category(left_category='Equity Scheme - Large Cap Fund', right_category='Equity Scheme - Large Cap Fund', left_returns=_returns(1, 10), right_returns=_returns(1, 10), left_benchmark_return_type='TRI', right_benchmark_return_type='TRI')
    assert result.status == 'INSUFFICIENT_DATA'
    assert result.left_equal_risk_return is None
