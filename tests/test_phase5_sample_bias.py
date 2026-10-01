"""Sample bias: whether a candidate generator's sample behaves like the market it generalises to.

The measurement has to be able to detect a bias that was planted and report none when none exists, and
both directions matter. A module that always finds bias is as useless as one that never does, so the
tests plant a known skew and then plant none.

The layering is the other half. `replay.py` selects three times over - materiality, event type, and
then the busiest names of the session - and a single number for "the sample is biased" would hide which
filter did it and therefore what to change.
"""
from __future__ import annotations

from datetime import date, timedelta

import duckdb
import pytest

from financial_brain.evaluation import sample_bias as SB

START = date(2020, 1, 1)


def _db(*, names=80, sessions=400, flagged=(), flagged_drift=0.0,
        materiality="high", event_type="RESULTS", filings_for=()):
    """A market where the flagged names can be given their own drift.

    ``flagged`` are the name indices that get an announcement every 10 sessions; ``flagged_drift`` is
    the extra per-session return they receive, so a study that works must recover its sign.
    ``filings_for`` get many filings on each announcement day, which is the layer the replay's
    ``ORDER BY filings DESC`` selects on.
    """
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE adjusted_prices (business_date DATE, lineage VARCHAR,
                   isin VARCHAR, close_adj DOUBLE)""")
    con.execute("""CREATE TABLE features (business_date DATE, lineage VARCHAR,
                   adv20 DOUBLE)""")
    con.execute("CREATE TABLE security_lineage (isin VARCHAR, lineage VARCHAR)")
    con.execute("""CREATE TABLE announcements (business_date DATE, isin VARCHAR,
                   company VARCHAR, materiality VARCHAR, event_type VARCHAR,
                   news_id VARCHAR)""")

    # The flagged set is a temp table joined once, not `IN (SELECT UNNEST(?))` inside a cross
    # product: DuckDB re-evaluated the UNNEST per row there, and building this 32,000-row fixture
    # took 120 seconds against 0.08 for the query it was built to exercise.
    con.execute("CREATE TEMP TABLE flags (i INTEGER)")
    if list(flagged):
        con.executemany("INSERT INTO flags VALUES (?)", [[int(i)] for i in flagged])
    con.execute(f"""
        CREATE TEMP TABLE grid AS
        SELECT n.i AS i, s.k AS k,
               CAST(? AS DATE) + CAST(s.k AS INTEGER) AS business_date,
               'L' || CAST(n.i AS VARCHAR) AS lineage,
               'INE' || LPAD(CAST(n.i AS VARCHAR), 9, '0') AS isin,
               (fl.i IS NOT NULL) AS flagged
        FROM generate_series(0, {sessions - 1}) AS s(k)
        CROSS JOIN generate_series(0, {names - 1}) AS n(i)
        LEFT JOIN flags fl ON fl.i = n.i
    """, [START])

    # Every name gets a deterministic idiosyncratic wiggle on top of any planted drift. Without it a
    # zero drift gives every name an identical price path, the per-session difference has no variance
    # at all, and the t-statistic is undefined rather than zero - so the test that matters most, the
    # one asserting no bias where none was planted, could not run.
    con.execute(f"""
        INSERT INTO adjusted_prices
        SELECT business_date, lineage, isin,
               100.0 * EXP(SUM(LN(1 + step)) OVER (PARTITION BY i ORDER BY k))
        FROM (SELECT *,
                     0.01 * SIN(i * 7.1 + k * 0.37)
                       + CASE WHEN flagged THEN {flagged_drift} ELSE 0.0 END AS step
              FROM grid)
    """)
    con.execute("INSERT INTO features SELECT business_date, lineage, 1e9 FROM grid")
    con.execute("INSERT INTO security_lineage SELECT DISTINCT isin, lineage FROM grid")
    # One filing every ten sessions for the flagged names, and extra copies for the busy ones.
    con.execute("CREATE TEMP TABLE busy (i INTEGER)")
    if list(filings_for):
        con.executemany("INSERT INTO busy VALUES (?)", [[int(i)] for i in filings_for])
    for extra in range(1 + (3 if filings_for else 0)):
        keep = "TRUE" if extra == 0 else "g.i IN (SELECT i FROM busy)"
        con.execute(f"""
            INSERT INTO announcements
            SELECT g.business_date, g.isin, g.lineage, ?, ?,
                   g.lineage || '-' || g.k || '-' || ?
            FROM grid g WHERE g.flagged AND g.k % 10 = 0 AND {keep}
        """, [materiality, event_type, str(extra)])
    return con


def _window(sessions=400):
    return {"start": START, "end": START + timedelta(days=sessions - 1)}


# ------------------------------------------------------------------- detecting a bias
def test_a_planted_skew_is_recovered_with_the_right_sign():
    con = _db(flagged=range(0, 40), flagged_drift=0.004)
    r = SB.layer(con, predicate="TRUE", horizon=20, **_window())
    assert r["t"] is not None, r["why"]
    assert r["mean_difference"] > 0.01, (
        f"names given +0.4%/session drift must read as a positive sample bias, got "
        f"{r['mean_difference']:+.2%}")
    assert r["t"] > 2


def test_a_negative_skew_reads_negative():
    con = _db(flagged=range(0, 40), flagged_drift=-0.004)
    r = SB.layer(con, predicate="TRUE", horizon=20, **_window())
    assert r["t"] is not None, r["why"]
    assert r["mean_difference"] < -0.01
    assert r["t"] < -2


def test_an_unbiased_sample_reads_as_no_bias():
    """As important as detecting one. A module that always finds bias is as useless as one that
    never does."""
    con = _db(flagged=range(0, 40), flagged_drift=0.0)
    r = SB.layer(con, predicate="TRUE", horizon=20, **_window())
    assert r["t"] is not None, r["why"]
    assert abs(r["mean_difference"]) < 0.002, (
        f"no planted drift should give no bias, got {r['mean_difference']:+.2%}")
    assert abs(r["t"]) < 2


def test_the_comparison_is_against_the_rest_of_the_same_session():
    """Otherwise a sample that merely holds high-beta names in a rising market reads as a good
    sample. The selected and unselected means are both reported so the bar is visible."""
    con = _db(flagged=range(0, 40), flagged_drift=0.004)
    r = SB.layer(con, predicate="TRUE", horizon=20, **_window())
    assert r["mean_selected"] > r["mean_rest"]
    assert r["mean_difference"] == pytest.approx(r["mean_selected"] - r["mean_rest"], abs=1e-9)


def test_the_t_is_over_sessions_not_over_selected_names():
    con = _db(flagged=range(0, 40), flagged_drift=0.003)
    r = SB.layer(con, predicate="TRUE", horizon=20, **_window())
    assert r["selected_names"] > r["sessions"], "there are more selected names than sessions"
    assert len(r["differences"]) == r["sessions"]
    assert "over sessions" in r["why"]


def test_a_sample_too_small_to_measure_says_so():
    con = _db(flagged=(1,), flagged_drift=0.004, sessions=100)
    r = SB.layer(con, predicate="TRUE", horizon=20, **_window(100))
    assert r["t"] is None
    assert "below" in r["why"]


# ---------------------------------------------------------------------- the layers
def test_each_filter_is_measured_separately_and_cumulatively():
    con = _db(flagged=range(0, 40), flagged_drift=0.002, filings_for=range(0, 10))
    out = SB.replay_sample(con, horizon=20, per_day=2, **_window())
    assert len(out["layers"]) == 4
    names = [x["layer"] for x in out["layers"]]
    assert names[0] == "any filing"
    assert "materiality" in names[1]
    assert "event types" in names[2]
    assert "busiest" in names[3]


def test_the_last_layer_selects_only_the_busiest_names_per_session():
    """The filter most likely to be accidental: ORDER BY filings DESC LIMIT 2 selects names having an
    unusually eventful day, which is a different population again."""
    con = _db(flagged=range(0, 40), flagged_drift=0.0, filings_for=range(0, 5))
    out = SB.replay_sample(con, horizon=20, per_day=2, **_window())
    last = out["layers"][-1]
    earlier = out["layers"][-2]
    assert last["top_n"] == 2
    if last["t"] is not None and earlier["t"] is not None:
        assert last["selected_per_session"] <= 2.0 + 1e-9
        assert last["selected_per_session"] < earlier["selected_per_session"]


def test_a_materiality_filter_that_matches_nothing_narrows_the_sample_to_nothing():
    con = _db(flagged=range(0, 40), flagged_drift=0.002, materiality="low")
    out = SB.replay_sample(con, horizon=20, per_day=2, **_window())
    assert out["layers"][0]["t"] is not None, "every filing is still selectable"
    assert out["layers"][1]["t"] is None, "none of them is high materiality"


def test_an_event_type_outside_the_replays_list_is_filtered_out():
    con = _db(flagged=range(0, 40), flagged_drift=0.002, event_type="SOMETHING_ELSE")
    out = SB.replay_sample(con, horizon=20, per_day=2, **_window())
    assert out["layers"][1]["t"] is not None, "high materiality still matches"
    assert out["layers"][2]["t"] is None, "the event type is not in the replay's seven"


def test_the_verdict_is_whether_the_final_sample_is_a_random_draw():
    con = _db(flagged=range(0, 40), flagged_drift=0.0, filings_for=range(0, 10))
    out = SB.replay_sample(con, horizon=20, per_day=2, **_window())
    if out["final"] is None:
        pytest.skip("the fixture produced no measurable final layer")
    assert out["representative"] == (abs(out["final"]["t"]) < 2.0)


def test_the_incremental_column_is_the_difference_between_consecutive_layers():
    con = _db(flagged=range(0, 40), flagged_drift=0.002, filings_for=range(0, 10))
    out = SB.replay_sample(con, horizon=20, per_day=2, **_window())
    usable = [x for x in out["layers"] if x.get("t") is not None]
    for inc, (a, b) in zip(out["incremental"], zip(usable, usable[1:])):
        assert inc["layer"] == b["layer"]
        assert inc["adds"] == pytest.approx(
            b["mean_difference"] - a["mean_difference"], abs=1e-12)


def test_the_replays_event_type_list_is_copied_not_imported():
    """So that a change to the replay's own list shows up as a difference between the two rather than
    silently changing what this module reports it measured."""
    from financial_brain.evaluation import replay

    for t in SB.REPLAY_EVENT_TYPES:
        assert f"'{t}'" in replay.CANDIDATE_SQL, (
            f"{t} is no longer in the replay's candidate query; the lists have diverged")


def test_striding_the_sessions_does_not_duplicate_them():
    """The bug that made every count sixteen times too large while the per-session averages still
    looked plausible. DISTINCT applies AFTER a window function, so numbering price rows and
    de-duplicating afterwards leaves one copy of each date per surviving row number."""
    con = _db(names=80, sessions=400, flagged=range(0, 40), flagged_drift=0.001)
    SB.prepare(con, horizon=20, stride=5, **_window())
    dupes = con.execute("""SELECT business_date, COUNT(*) FROM _sessions
                           GROUP BY 1 HAVING COUNT(*) > 1""").fetchall()
    assert not dupes, f"_sessions holds duplicate dates: {dupes[:3]}"

    total = con.execute(
        "SELECT COUNT(DISTINCT business_date) FROM adjusted_prices").fetchone()[0]
    kept = con.execute("SELECT COUNT(*) FROM _sessions").fetchone()[0]
    assert kept == pytest.approx(total / 5, abs=1), f"{kept} of {total} sessions at stride 5"

    # And the pool that is built from it holds one row per (session, lineage).
    fan = con.execute("""SELECT business_date, lineage, COUNT(*) FROM _pool
                         GROUP BY 1, 2 HAVING COUNT(*) > 1""").fetchall()
    assert not fan, f"the pool fanned out: {fan[:3]}"


def test_the_busiest_layer_selects_exactly_the_requested_number_per_session():
    """A count that survives the fanout check: forty names announce, two are kept."""
    con = _db(names=80, sessions=400, flagged=range(0, 40), flagged_drift=0.0,
              filings_for=range(0, 5))
    out = SB.replay_sample(con, horizon=20, per_day=2, stride=5, **_window())
    last = out["layers"][-1]
    earlier = out["layers"][-2]
    assert last["selected_per_session"] == pytest.approx(2.0, abs=1e-9)
    assert earlier["selected_per_session"] == pytest.approx(40.0, abs=1e-9)
