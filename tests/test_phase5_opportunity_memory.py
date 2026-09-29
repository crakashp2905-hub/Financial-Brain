"""Opportunity memory: what the system declined, and whether it was right to.

The whole value is in the rejected rows, so the tests are mostly about them: that they are recorded
at all, that the three kinds of rejection stay apart, that the counterfactual is resolved against the
same bar the accepted names are measured against, and that "the window has not closed" never gets
collapsed into "this name stopped trading".
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from financial_brain.opportunity import memory as mem
from financial_brain.paper import engine
from tests.test_phase4_paper_engine import START, _db

MEMORY_DDL = """CREATE TABLE opportunity_memory (
    run_id VARCHAR NOT NULL, session DATE NOT NULL, signal_from DATE NOT NULL,
    lineage VARCHAR NOT NULL, rank INTEGER, score DOUBLE, disposition VARCHAR NOT NULL,
    reason VARCHAR, fwd_return DOUBLE, fwd_excess DOUBLE, resolved_at TIMESTAMPTZ,
    unresolvable BOOLEAN, PRIMARY KEY (run_id, session, lineage))"""


def _con(**kw):
    con = _db(**{"names": 8, "sessions": 200, "drift": 0.004, **kw})
    con.execute(MEMORY_DDL)
    return con


def _run(con, **kw):
    base = dict(feature="dist_52w_high", start=START, end=START + timedelta(days=199),
                rebalance=20, max_positions=3, simulate_fills=False, remember=True)
    base.update(kw)
    return engine.run(con, **base)


def _rows(con, run_id, **where):
    clause = "".join(f" AND {k} = ?" for k in where)
    return con.execute(
        f"""SELECT lineage, rank, score, disposition, reason, fwd_return, fwd_excess,
                   unresolvable, session
            FROM opportunity_memory WHERE run_id = ?{clause}""",
        [run_id, *where.values()]).fetchall()


# ----------------------------------------------------------------- the rejections exist
def test_the_rejected_candidates_are_recorded_not_only_the_holdings():
    """A book that logs only its holdings learns from a sample it selected itself."""
    con = _con()
    r = _run(con)
    assert r.remembered > 0
    rows = _rows(con, r.experiment_id)
    taken = [x for x in rows if x[3] in mem.ACCEPTANCES]
    rejected = [x for x in rows if x[3] in mem.REJECTIONS]
    assert taken and rejected
    assert len(rejected) > len(taken), (
        "a top-3 book over eight names must reject more than it takes")


def test_every_candidate_the_ranking_saw_is_recorded_once_per_rebalance():
    con = _con()
    r = _run(con)
    per_session = con.execute(
        """SELECT session, COUNT(*), COUNT(DISTINCT lineage)
           FROM opportunity_memory WHERE run_id = ? GROUP BY session""",
        [r.experiment_id]).fetchall()
    assert per_session
    for _s, n, distinct in per_session:
        assert n == distinct, "a lineage must appear at most once per rebalance"
        assert n >= 3


def test_the_three_rejections_stay_apart_because_they_are_different_failures():
    """Ranked below the cut is the strategy working; a filter firing is a rule acting. Only the
    second can be reconsidered without changing the strategy."""
    con = _con(names=8)
    days = [x[0] for x in con.execute(
        "SELECT DISTINCT business_date FROM adjusted_prices ORDER BY 1").fetchall()]
    cols = con.execute("PRAGMA table_info('event_flags')").fetchall() if False else None
    con.execute("""CREATE TABLE event_flags (business_date DATE, lineage VARCHAR,
                   e_scheme BOOLEAN)""")
    for d in days[::10]:
        con.execute("INSERT INTO event_flags VALUES (?, 'L7', TRUE)", [d])

    r = _run(con, exclude_events=("e_scheme",))
    dispositions = {x[3] for x in _rows(con, r.experiment_id)}
    assert mem.REJECTED_FILTER in dispositions
    assert mem.REJECTED_RANK in dispositions
    filtered = [x for x in _rows(con, r.experiment_id) if x[3] == mem.REJECTED_FILTER]
    assert all(x[0] == "L7" for x in filtered)
    assert all("event exclusion" in x[4] for x in filtered)
    assert cols is None


def test_a_rejection_carries_the_rank_and_score_that_produced_it():
    con = _con()
    r = _run(con)
    ranked = [x for x in _rows(con, r.experiment_id) if x[3] == mem.REJECTED_RANK]
    assert ranked
    for _lineage, rank, score, _d, reason, *_ in ranked:
        assert rank is not None and rank > 0
        assert score is not None
        assert str(rank) in reason, "the reason should name the rank that excluded it"


def test_re_running_the_same_experiment_overwrites_its_memory_rather_than_doubling_it():
    con = _con()
    a = _run(con)
    first = len(_rows(con, a.experiment_id))
    b = _run(con)
    assert a.experiment_id == b.experiment_id
    assert len(_rows(con, b.experiment_id)) == first


def test_rows_without_a_lineage_or_disposition_are_refused():
    con = _con()
    with pytest.raises(mem.MemoryError_, match="without a lineage"):
        mem.record(con, run_id="X", session=START, signal_from=START,
                   rows=[{"lineage": "L0"}])


# ------------------------------------------------------------------ the counterfactual
def test_resolving_measures_a_rejected_name_against_the_same_bar_as_a_taken_one():
    """The question is not whether a rejected name went up, but whether it went up more than the
    alternatives the ranking had in front of it."""
    con = _con(sessions=300)
    r = _run(con, end=START + timedelta(days=299))
    out = mem.resolve(con, run_id=r.experiment_id, horizon=20)
    assert out["resolved"] > 0

    by_session = {}
    for _lineage, _rank, _sc, _d, _why, ret, exc, _u, session in _rows(con, r.experiment_id):
        if ret is not None:
            by_session.setdefault(session, []).append((ret, exc))
    for session, vals in by_session.items():
        if len(vals) < 2:
            continue
        # excess is return minus the mean of everything considered that session
        mean_ret = sum(v[0] for v in vals) / len(vals)
        for ret, exc in vals:
            assert exc == pytest.approx(ret - mean_ret, abs=1e-9), session


def test_a_window_that_has_not_closed_is_pending_not_unresolvable():
    """Collapsing them quietly drops the most recent rebalances."""
    con = _con(sessions=200)
    r = _run(con)
    out = mem.resolve(con, run_id=r.experiment_id, horizon=60)
    assert out["pending"] > 0, "the last rebalances cannot have a 60-session forward return yet"
    assert out["resolved"] + out["unresolvable"] + out["pending"] == out["rows"]


def test_a_name_that_stopped_trading_is_unresolvable_not_pending():
    """Delistings are exactly the names a rejection rule should be credited for avoiding, so they
    must not sit in the pending bucket forever."""
    con = _con(sessions=300)
    days = [x[0] for x in con.execute(
        "SELECT DISTINCT business_date FROM adjusted_prices ORDER BY 1").fetchall()]
    con.execute("DELETE FROM adjusted_prices WHERE lineage = 'L1' AND business_date > ?",
                [days[120]])
    r = _run(con, end=START + timedelta(days=299))
    out = mem.resolve(con, run_id=r.experiment_id, horizon=20)
    dead = [x for x in _rows(con, r.experiment_id, lineage="L1") if x[7]]
    assert dead, "the delisted name must be marked unresolvable somewhere"
    assert out["unresolvable"] > 0


def test_resolving_twice_does_not_change_the_answer():
    con = _con(sessions=300)
    r = _run(con, end=START + timedelta(days=299))
    a = mem.resolve(con, run_id=r.experiment_id, horizon=20)
    b = mem.resolve(con, run_id=r.experiment_id, horizon=20)
    assert (a["resolved"], a["unresolvable"]) == (b["resolved"], b["unresolvable"])


# ---------------------------------------------------------------------- the report
def test_the_rejection_edge_is_negative_when_the_ranking_works():
    """The headline. A ranking that works declines names that then do worse than the ones it took;
    positive means it is discarding the better half.

    The fixture gives name i a drift proportional to i and ranks on a feature equal to i, so the
    ranking is exactly right by construction and the edge must be negative.
    """
    con = _con(names=10, sessions=300, drift=0.01)
    r = _run(con, feature="mom_12_1", max_positions=3, end=START + timedelta(days=299))
    mem.resolve(con, run_id=r.experiment_id, horizon=20)
    rep = mem.report(con, run_id=r.experiment_id)
    assert rep["rejection_edge"] is not None
    assert rep["rejection_edge"] < 0, (
        f"a correct ranking should decline the worse names; edge {rep['rejection_edge']:+.2%}")
    assert rep["taken_mean_excess"] > rep["rejected_mean_excess"]


def test_the_report_counts_false_negatives_and_positives_with_their_rates():
    con = _con(names=10, sessions=300, drift=0.01)
    r = _run(con, feature="mom_12_1", max_positions=3, end=START + timedelta(days=299))
    mem.resolve(con, run_id=r.experiment_id, horizon=20)
    rep = mem.report(con, run_id=r.experiment_id)
    assert rep["false_negatives"] >= 0 and rep["false_positives"] >= 0
    assert 0.0 <= rep["false_negative_rate"] <= 1.0
    assert 0.0 <= rep["false_positive_rate"] <= 1.0
    assert rep["rejected_n"] > 0 and rep["taken_n"] > 0


def test_reporting_before_resolving_is_refused_rather_than_returning_zeroes():
    con = _con()
    r = _run(con)
    with pytest.raises(mem.MemoryError_, match="resolve"):
        mem.report(con, run_id=r.experiment_id)


def test_by_reason_groups_the_rejections_so_a_bad_rule_becomes_visible():
    con = _con(names=10, sessions=300, drift=0.01)
    r = _run(con, feature="mom_12_1", max_positions=3, end=START + timedelta(days=299))
    mem.resolve(con, run_id=r.experiment_id, horizon=20)
    rows = mem.by_reason(con, run_id=r.experiment_id)
    for row in rows:
        assert row["disposition"] in mem.REJECTIONS
        assert row["n"] >= 20
        assert 0.0 <= row["win_rate"] <= 1.0


def test_a_sell_is_recorded_as_an_exit_not_as_a_name_the_book_passed_over():
    """Pooling exits with the thousands of names never owned makes the one disposition that measures
    selling decisions measure delistings instead."""
    con = _con(names=10, sessions=300, drift=0.01)
    days = [x[0] for x in con.execute(
        "SELECT DISTINCT business_date FROM adjusted_prices ORDER BY 1").fetchall()]
    # Flip the ranking halfway so everything currently held falls out of the target.
    con.execute("UPDATE features SET mom_12_1 = -mom_12_1 WHERE business_date >= ?",
                [days[150]])

    r = _run(con, feature="mom_12_1", max_positions=3, end=START + timedelta(days=299))
    rows = _rows(con, r.experiment_id)
    exits = [x for x in rows if x[3] == mem.EXITED]
    assert exits, "reversing the ranking must produce sells"
    assert any("ranked out at" in (x[4] or "") for x in exits), (
        "a name that ranked out while held is an exit with a rank, not a vanished name")

    # And a name never held that ranks badly is still a plain rank rejection.
    passed_over = [x for x in rows if x[3] == mem.REJECTED_RANK]
    assert passed_over
    assert all("ranked " in (x[4] or "") for x in passed_over)


def test_an_exit_and_a_pass_over_are_never_the_same_row():
    con = _con(names=10, sessions=300, drift=0.01)
    r = _run(con, feature="mom_12_1", max_positions=3, end=START + timedelta(days=299))
    per = con.execute(
        """SELECT session, lineage, COUNT(DISTINCT disposition)
           FROM opportunity_memory WHERE run_id = ? GROUP BY 1, 2 HAVING COUNT(*) > 1""",
        [r.experiment_id]).fetchall()
    assert not per, f"a lineage has two dispositions in one session: {per[:3]}"
