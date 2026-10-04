import pytest

from financial_brain.evaluation import half_life as H


def test_known_decay_is_recovered():
    ic = {1: 0.02, 5: 0.06, 10: 0.08, 20: 0.06, 40: 0.03, 60: 0.015, 120: 0.0}
    r = H.half_life(ic)
    assert r["peak_h"] == 10 and r["half_life"] == pytest.approx(33.3, abs=0.5)   # 0.04 lies between 20 (0.06) and 40 (0.03)


def test_a_signal_still_rising_has_no_half_life():
    r = H.half_life({1: 0.01, 5: 0.02, 20: 0.04, 60: 0.06, 120: 0.07})
    assert r["half_life"] is None and r["still_rising"] and r["peak_h"] == 120


def test_negative_signal_is_measured_in_its_own_direction():
    r = H.half_life({1: -0.01, 5: -0.04, 10: -0.05, 20: -0.02, 60: -0.005})
    assert r["direction"] == -1.0 and r["peak_h"] == 10 and r["peak_ic"] == pytest.approx(-0.05)


def test_no_positive_ic_is_stated_not_invented():
    r = H.half_life({1: 0.0, 5: 0.0, 20: 0.0})
    assert r["peak_h"] is None and r["half_life"] is None


def test_too_few_horizons_refused():
    with pytest.raises(H.HalfLifeError):
        H.half_life({1: 0.1, 5: 0.2})
