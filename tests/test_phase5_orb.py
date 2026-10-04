import numpy as np
import pytest

from financial_brain.evaluation import orb as O


def day(path_after, base=100.0, or_hi=101.0, or_lo=99.0):
    """Minutes 555..: a 15-minute opening range between or_lo and or_hi, then ``path_after`` closes."""
    m = list(range(555, 570))
    o = [base] * 15
    h = [or_hi] + [base + 0.2] * 14
    l = [or_lo] + [base - 0.2] * 14
    c = [base] * 15
    prev = base
    for i, px in enumerate(path_after):
        m.append(570 + i)
        o.append(prev)
        h.append(max(prev, px) + 0.01)
        l.append(min(prev, px) - 0.01)
        c.append(px)
        prev = px
    return [np.array(a, dtype=float) for a in (m, o, h, l, c)]


def test_a_breakout_that_runs_hits_the_target_and_earns_two_r():
    # closes above 101 at minute 570, next open ~101.5; stop 99 (R~2.5); target ~106.5
    path = [101.5, 102, 103, 104, 105, 106, 107, 107.5] + [107.5] * 30
    t = O.simulate_day(*day(path), side="long")
    assert t["why"] == "target" and t["r"] == pytest.approx(2.0, abs=0.15)


def test_a_failed_breakout_stops_out_near_minus_one_r():
    path = [101.5, 101.2, 100.5, 99.5, 98.0] + [98.0] * 30
    t = O.simulate_day(*day(path), side="long")
    assert t["why"] == "stop" and -1.4 < t["r"] < -0.9


def test_a_gap_through_the_stop_fills_at_the_open_not_the_stop():
    m, o, h, l, c = day([101.5, 101.4])
    m = np.append(m, 572); o = np.append(o, 97.0); h = np.append(h, 97.2); l = np.append(l, 96.5); c = np.append(c, 97.0)
    t = O.simulate_day(m, o, h, l, c, side="long")
    assert t["why"] == "stop" and t["exit"] == 97.0 and t["r"] < -1.5


def test_no_trigger_means_no_trade_and_the_trigger_must_be_a_close_not_a_wick():
    assert O.simulate_day(*day([100.5] * 30), side="long") is None
    m, o, h, l, c = day([100.5] * 30)
    h[16] = 105.0                                               # a wick through the range, close inside
    assert O.simulate_day(m, o, h, l, c, side="long") is None


def test_short_side_mirrors_the_long():
    path = [98.5, 98, 97, 96, 95, 94, 93, 92.5] + [92.5] * 30
    t = O.simulate_day(*day(path), side="short")
    assert t["why"] == "target" and t["ret"] > 0


def test_time_exit_when_neither_stop_nor_target_is_reached():
    path = [101.5] + [101.6] * 350
    t = O.simulate_day(*day(path), side="long")
    assert t["why"] == "time" and abs(t["r"]) < 0.2


def test_bad_arguments_and_degenerate_days():
    with pytest.raises(O.OrbError):
        O.simulate_day(*day([101.5]), side="up")
    assert O.simulate_day(np.arange(555, 560.0), *(np.ones(5),) * 4, side="long") is None
