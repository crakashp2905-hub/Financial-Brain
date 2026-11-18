"""The swing lesson gate controls for how many groups it looked at: a noisy "bad" group is a story, not a rule."""
from __future__ import annotations

import pytest

from financial_brain.config import Config
from financial_brain.decisions import postmortem as pm
from financial_brain.storage.db import Database


@pytest.fixture
def con(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    with db.connect() as c:
        yield c


def _pm(con, did, excess, *, regime="NEUTRAL", prompted_by="ORDER_WIN", bucket="liquid", action="BUY", fired=False):
    con.execute("""INSERT INTO decision_postmortems (decision_id, excess, regime, prompted_by, bucket, action,
                   invalidation_fired, features, created_at) VALUES (?,?,?,?,?,?,?, '{}', NOW())""",
                [did, excess, regime, prompted_by, bucket, action, fired])


def test_welch_p_edge_cases():
    assert pm.welch_p_worse([-0.1], [0.1, 0.2]) == 1.0                 # one observation is not a test
    assert pm.welch_p_worse([-0.1, -0.1], [0.1, 0.1]) == 0.0           # no spread, clearly lower
    assert pm.welch_p_worse([0.1, 0.1], [0.1, 0.1]) == 1.0             # no spread, no difference
    assert pm.welch_p_worse([0.2, 0.3, 0.25], [-0.1, -0.2, -0.15]) > 0.99   # better, not worse


def test_bh_adjust_is_monotone_and_never_below_the_raw_p():
    p = [0.001, 0.04, 0.03, 0.5, 0.2]
    q = pm.bh_adjust(p)
    assert all(qi >= pi for qi, pi in zip(q, p, strict=True))
    assert q[0] == pytest.approx(0.005) and q[3] == 0.5
    order = sorted(range(len(p)), key=lambda i: p[i])
    assert [q[i] for i in order] == sorted(q)


def test_a_noisy_gap_that_the_old_rule_would_have_confirmed_is_not_a_lesson(con):
    """Five RISK_OFF trades averaging -5% but swinging +-25%, against twelve around zero: a 5% gap and 5 trades, which
    the count-and-gap rule alone would call a rule. Against the spread of the data it is not distinguishable."""
    for i, x in enumerate((-0.30, 0.20, -0.25, 0.15, -0.05)):
        _pm(con, f"o{i}", x, regime="RISK_OFF")
    for i, x in enumerate((-0.2, 0.2, 0.0, 0.1, -0.1, 0.15, -0.15, 0.05, -0.05, 0.1, -0.1, 0.0)):
        _pm(con, f"n{i}", x, regime="RISK_ON")
    lesson = next(x for x in pm.lessons(con) if x.feature == "regime=RISK_OFF")
    assert lesson.n >= pm.MIN_SUPPORT and lesson.gap <= -pm.MATERIAL      # old rule: confirmed
    assert lesson.q is not None and lesson.q > pm.FDR_Q
    assert not lesson.confirmed()
    assert pm.gate(con, regime="RISK_OFF") == []


def test_a_clean_separation_still_becomes_a_rule_and_says_how_sure_it_is(con):
    for i in range(8):
        _pm(con, f"r{i}", -0.06 - 0.002 * i, regime="RISK_OFF")
    for i in range(8):
        _pm(con, f"n{i}", 0.04 + 0.002 * i, regime="RISK_ON")
    lesson = next(x for x in pm.lessons(con) if x.feature == "regime=RISK_OFF")
    assert lesson.confirmed() and lesson.q is not None and lesson.q < 0.01
    assert "(q " in lesson.describe()


def test_many_looks_make_each_one_harder_to_confirm(con):
    """The same moderately bad group is confirmed when it is one of few features and not when it is one of many."""
    bad = (-0.12, -0.08, -0.10, -0.06, -0.14, -0.09)
    rest = (0.02, -0.02, 0.04, 0.0, 0.03, -0.01, 0.01, 0.02)
    for i, x in enumerate(bad):
        _pm(con, f"b{i}", x, bucket="illiquid")
    for i, x in enumerate(rest):
        _pm(con, f"g{i}", x, bucket="liquid")
    few = next(x for x in pm.lessons(con) if x.feature == "liquidity=illiquid")
    assert few.confirmed()
    # many more groups, all noise: the bad group's q-value can only get worse
    for j in range(12):
        for i in range(6):
            _pm(con, f"z{j}_{i}", 0.01 * ((i + j) % 3 - 1), prompted_by=f"EVENT{j}", regime=f"R{j}")
    many = next(x for x in pm.lessons(con) if x.feature == "liquidity=illiquid")
    assert many.q >= few.q
