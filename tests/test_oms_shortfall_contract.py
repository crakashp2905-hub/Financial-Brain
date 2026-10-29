import pytest

from financial_brain.execution.oms import model as M
from financial_brain.execution.oms import shortfall as S


def order(*, side=M.BUY, qty=10, filled=0, px=None, arrival=100.0):
    return M.Order("id", "s", "2026-10-29", "X", "NSE", side, qty, 100.0,
                   filled_qty=filled, avg_price=px, arrival_price=arrival)


def test_weighted_parent_shortfall_and_noncompletion():
    a = order(qty=10, filled=5, px=101.0)
    b = order(qty=10, filled=5, px=103.0)
    got = S.realised_bp([a, b])
    assert got["avg_price"] == 102.0
    assert got["realised_bp"] == pytest.approx(200.0)
    assert got["unfilled_share"] == .5
    assert S.realised_bp([order()])["unfilled_share"] == 1.0


def test_sell_sign_and_missing_benchmark_fail_closed():
    assert S.realised_bp([order(side=M.SELL, filled=10, px=99.0)])["realised_bp"] == pytest.approx(100.0)
    with pytest.raises(ValueError, match="arrival benchmark"):
        S.realised_bp([order(filled=5, px=101), order(filled=5, px=102, arrival=99)])
    with pytest.raises(ValueError, match="one side"):
        S.realised_bp([order(filled=5, px=101), order(side=M.SELL, filled=5, px=99)])
    with pytest.raises(ValueError, match="missing"):
        S.realised_bp([order(filled=5, px=None)])


def test_zero_predicted_shortfall_is_not_dropped_from_comparison():
    result = S.summary([{"realised_bp": 10.0, "predicted_bp": 0.0}])
    assert result["compared"] == 1 and result["median_predicted_bp"] == 0.0
    assert result["median_ratio"] is None
