"""The two process-level experiments, and the leaks they have to avoid.

A walk-forward that selects on data it later reports on is not a walk-forward, and the number it
produces is indistinguishable from an honest one by inspection. So most of this file is about the fold
geometry: train strictly before test, a purge gap at least as wide as the longest horizon, and a
selection made from the training column only.

The baselines are simpler but carry one trap of their own - a comparison inside the engine's own
calibration floor is not a comparison, and the module has to say so rather than rank noise.
"""
from __future__ import annotations


import pytest

from financial_brain.evaluation import economic as E
from tests.test_phase4_paper_engine import _db


def _con(sessions=900, names=8):
    return _db(names=names, sessions=sessions, drift=0.004)


def _cal(con):
    return [r[0] for r in con.execute(
        "SELECT DISTINCT business_date FROM adjusted_prices ORDER BY 1").fetchall()]


# --------------------------------------------------------------------- the fold geometry
def test_training_always_ends_before_testing_begins():
    con = _con()
    cal = _cal(con)
    fs = E.folds(con, start=cal[0], end=cal[-1], initial_train=300, test_len=150,
                 horizons=(20, 60))
    assert fs
    for f in fs:
        assert f.train[1] < f.test[0], f"fold {f.index} trains past its test window"


def test_a_purge_gap_at_least_the_longest_horizon_separates_them():
    """The last rebalance inside training holds positions whose returns land in the test window.
    Without the gap the training set's tail *is* the test set's head."""
    con = _con()
    cal = _cal(con)
    fs = E.folds(con, start=cal[0], end=cal[-1], initial_train=300, test_len=150,
                 horizons=(20, 60, 120))
    for f in fs:
        assert f.gap_sessions >= 120
        held = len([d for d in cal if f.train[1] < d < f.test[0]])
        assert held >= f.gap_sessions - 1, (
            f"fold {f.index} holds {held} sessions between train and test, not {f.gap_sessions}")


def test_the_window_expands_rather_than_rolling():
    """The horizon choice should use all the history available at the time; a rolling window would
    discard the early years for symmetry alone."""
    con = _con()
    cal = _cal(con)
    fs = E.folds(con, start=cal[0], end=cal[-1], initial_train=300, test_len=150,
                 horizons=(20,))
    assert len(fs) > 1
    assert all(f.train[0] == fs[0].train[0] for f in fs), "the start should not move"
    ends = [f.train[1] for f in fs]
    assert ends == sorted(ends) and len(set(ends)) == len(ends)


def test_test_windows_do_not_overlap_one_another():
    con = _con()
    cal = _cal(con)
    fs = E.folds(con, start=cal[0], end=cal[-1], initial_train=300, test_len=150,
                 horizons=(20,))
    for a, b in zip(fs, fs[1:]):
        assert a.test[1] < b.test[0], "an observation must not be scored in two folds"


def test_a_window_too_short_for_one_fold_is_refused():
    con = _con(sessions=300)
    cal = _cal(con)
    with pytest.raises(ValueError, match="cannot carry"):
        E.folds(con, start=cal[0], end=cal[-1], initial_train=250, test_len=200,
                horizons=(120,))


# ------------------------------------------------------------------- the selection itself
def test_the_choice_is_made_from_the_training_column_only():
    con = _con(sessions=900)
    cal = _cal(con)
    w = E.walk_forward_selection(con, start=cal[0], end=cal[-1], horizons=(20, 60),
                                 initial_train=300, test_len=150, max_positions=4,
                                 min_adv=0.0)
    assert w["n_folds"] >= 1
    for f in w["folds"]:
        if f["chosen"] is None:
            continue
        train = f["chosen_on"]
        best = max(train, key=lambda h: train[h]["excess"])
        assert f["chosen"] == best, (
            "the chosen horizon must be the best on TRAIN, whatever the test column says")


def test_the_out_of_sample_column_is_reported_for_every_horizon_not_only_the_chosen_one():
    """Otherwise there is no way to tell whether the selection added anything over a fixed choice."""
    con = _con(sessions=900)
    cal = _cal(con)
    w = E.walk_forward_selection(con, start=cal[0], end=cal[-1], horizons=(20, 60),
                                 initial_train=300, test_len=150, max_positions=4,
                                 min_adv=0.0)
    assert set(w["oos_excess_by_fixed_horizon"]) == {20, 60}
    assert w["best_fixed_horizon"] in (20, 60, None)
    assert "selection_beat_best_fixed" in w


def test_a_selection_that_never_changes_is_reported_as_such():
    con = _con(sessions=900)
    cal = _cal(con)
    w = E.walk_forward_selection(con, start=cal[0], end=cal[-1], horizons=(20,),
                                 initial_train=300, test_len=150, max_positions=4,
                                 min_adv=0.0)
    assert w["changed_its_mind"] is False
    assert set(w["chosen_per_fold"]) <= {20, None}


def test_the_folds_carry_their_own_windows_so_a_result_can_be_rebuilt():
    con = _con(sessions=900)
    cal = _cal(con)
    w = E.walk_forward_selection(con, start=cal[0], end=cal[-1], horizons=(20,),
                                 initial_train=300, test_len=150, max_positions=4,
                                 min_adv=0.0)
    for f in w["folds"]:
        assert f["train"][0] < f["train"][1] < f["test"][0] < f["test"][1]
        assert f["gap_sessions"] >= 20


# ----------------------------------------------------------------------- the baselines
def test_every_baseline_runs_through_the_same_engine_and_control():
    con = _con(sessions=600)
    cal = _cal(con)
    b = E.baselines(con, start=cal[0], end=cal[-1], rebalance=20, max_positions=4,
                    min_adv=0.0, which=(("mom_12_1", 1, "momentum"),
                                        ("dist_52w_high", 1, "the candidate")),
                    indices=("Nifty 500",))
    assert set(b["strategies"]) == {"mom_12_1", "dist_52w_high"}
    for row in b["strategies"].values():
        assert row is not None
        assert "excess" in row and "gross_excess" in row
        assert row["universe_return"] is not None


def test_the_calibration_floor_is_reported_and_used_to_flag_meaningless_rankings():
    """A baseline comparison inside the engine's own resolution is not a comparison."""
    con = _con(sessions=600)
    cal = _cal(con)
    b = E.baselines(con, start=cal[0], end=cal[-1], rebalance=20, max_positions=4,
                    min_adv=0.0, which=(("dist_52w_high", 1, "the candidate"),),
                    indices=())
    assert "floor" in b
    assert isinstance(b["inside_the_floor"], list)
    for k in b["inside_the_floor"]:
        assert abs(b["strategies"][k]["excess"]) <= abs(b["floor"])


def test_the_ranking_is_by_excess_over_the_universe_not_by_total_return():
    """Total return ranks whatever held the most market beta. The universe is the comparison."""
    con = _con(sessions=600)
    cal = _cal(con)
    b = E.baselines(con, start=cal[0], end=cal[-1], rebalance=20, max_positions=4,
                    min_adv=0.0, which=(("mom_12_1", 1, "m"), ("mom_12_1", -1, "inverse"),
                                        ("dist_52w_high", 1, "d")),
                    indices=())
    order = b["ranking"]
    excesses = [b["strategies"][k]["excess"] for k in order]
    assert excesses == sorted(excesses, reverse=True)


def test_a_feature_this_archive_does_not_carry_is_recorded_as_unavailable():
    """It arrives as a database binder error, which is a statement about the archive rather than a
    bug. Aborting the comparison would hide the members that did run; scoring it zero is worse."""
    con = _con(sessions=600)
    cal = _cal(con)
    b = E.baselines(con, start=cal[0], end=cal[-1], rebalance=20, max_positions=4,
                    min_adv=0.0,
                    which=(("dist_52w_high", 1, "d"), ("no_such_feature", 1, "absent")),
                    indices=())
    assert b["strategies"]["no_such_feature"] is None
    assert b["strategies"]["dist_52w_high"] is not None
    assert "no_such_feature" not in b["ranking"]


def test_the_baselines_that_cannot_be_built_are_named_rather_than_faked():
    """No point-in-time fundamentals or sector labels exist here, and a value baseline assembled from
    present-day data would be easier to beat - the wrong direction to be wrong in."""
    con = _con(sessions=600)
    cal = _cal(con)
    b = E.baselines(con, start=cal[0], end=cal[-1], rebalance=20, max_positions=4,
                    min_adv=0.0, which=(("dist_52w_high", 1, "d"),), indices=())
    assert "naive value" in b["missing"] and "naive quality" in b["missing"]
    assert "point-in-time fundamentals" in b["why_missing"]


def test_both_a_net_and_a_gross_excess_are_reported_for_each_baseline():
    """Net says whether it is tradeable; gross says whether there was a signal to trade."""
    con = _con(sessions=600)
    cal = _cal(con)
    b = E.baselines(con, start=cal[0], end=cal[-1], rebalance=20, max_positions=4,
                    min_adv=0.0, which=(("dist_52w_high", 1, "d"),), indices=())
    row = b["strategies"]["dist_52w_high"]
    assert row["gross_excess"] > row["excess"], "costs must make the net figure the smaller one"


def test_a_feature_the_engine_refuses_is_recorded_as_refused_not_as_zero():
    con = _con(sessions=120)
    cal = _cal(con)
    # A 250-session rebalance in a 120-session window cannot run.
    b = E.baselines(con, start=cal[0], end=cal[-1], rebalance=250, max_positions=4,
                    min_adv=0.0, which=(("dist_52w_high", 1, "d"),), indices=())
    assert b["strategies"]["dist_52w_high"] is None
    assert b["ranking"] == []
