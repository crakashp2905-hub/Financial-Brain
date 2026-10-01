"""The momentum-matched event study, and the exclusion hook it produced.

An unmatched event study rediscovers that events happen to stocks that were already moving, so the
matching *is* the experiment and most of this file tests it: that the control comes from the same
session and the same bucket, that the statistic is computed over sessions rather than over events, and
that an effect is judged against the multiple-testing bar rather than against 1.96.
"""
from __future__ import annotations

from datetime import date, timedelta

import duckdb
import pytest

from financial_brain.evaluation import events as EV
from financial_brain.paper import engine

START = date(2020, 1, 1)


def _db(*, names=120, sessions=400, event_every=0, event_names=(),
        event_col="e_scheme", event_return=0.0, momentum_return=0.0):
    """A market where momentum and the event can be dialled independently.

    ``momentum_return`` is added to the top half by momentum rank and ``event_return`` to names
    carrying the event, so a study that fails to difference out momentum will attribute the first to
    the second.

    120 names, not 40: the study requires at least five controls and one event inside each
    (session, decile) cell, and ten deciles over forty names leaves four per cell - so every cell was
    discarded and the interesting tests skipped rather than ran. Rows are built in SQL because 120
    names over 400 sessions is 48,000 inserts, and this project has measured DuckDB's executemany at
    roughly 800 rows a second.
    """
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE adjusted_prices (business_date DATE, lineage VARCHAR,
                   isin VARCHAR, close_adj DOUBLE)""")
    con.execute("""CREATE TABLE features (business_date DATE, lineage VARCHAR, adv20 DOUBLE,
                   mom_12_1 DOUBLE, ret_20d DOUBLE, dist_52w_high DOUBLE)""")
    cols = ", ".join(f"{c} BOOLEAN" for c in EV.EVENTS)
    con.execute(f"""CREATE TABLE event_flags (business_date DATE, lineage VARCHAR, {cols})""")

    # Which (name, session) pairs carry the event: every `event_every` sessions, for the named ranks.
    picks = sorted(event_names)
    con.execute("CREATE TEMP TABLE picks (i INTEGER)")
    for i in picks:
        con.execute("INSERT INTO picks VALUES (?)", [i])

    con.execute(f"""
        CREATE TEMP TABLE grid AS
        SELECT n.i AS i, s.k AS k,
               CAST(? AS DATE) + CAST(s.k AS INTEGER) AS business_date,
               'L' || CAST(n.i AS VARCHAR) AS lineage,
               (n.i >= {names // 2}) AS high_mom,
               ({event_every} > 0 AND s.k % {max(event_every, 1)} = 0 AND s.k >= 40
                AND n.i IN (SELECT i FROM picks)) AS fires
        FROM generate_series(0, {sessions - 1}) AS s(k),
             generate_series(0, {names - 1}) AS n(i)
    """, [START])

    # Compound the per-session step into a price path.
    con.execute(f"""
        INSERT INTO adjusted_prices
        SELECT business_date, lineage,
               'INE' || LPAD(CAST(i AS VARCHAR), 9, '0'),
               100.0 * EXP(SUM(LN(1 + step)) OVER (PARTITION BY i ORDER BY k))
        FROM (SELECT *,
                     (CASE WHEN high_mom THEN {momentum_return} ELSE 0.0 END)
                   + (CASE WHEN fires THEN {event_return} ELSE 0.0 END) AS step
              FROM grid)
    """)
    con.execute("""INSERT INTO features
                   SELECT business_date, lineage, 1e9,
                          CAST(i AS DOUBLE), CAST(i % 7 AS DOUBLE), CAST(-i AS DOUBLE)
                   FROM grid""")
    vals = ", ".join("TRUE" if c == event_col else "FALSE" for c in EV.EVENTS)
    con.execute(f"""INSERT INTO event_flags
                    SELECT business_date, lineage, {vals} FROM grid WHERE fires""")
    return con


def _ledger(con):
    con.execute("""CREATE TABLE IF NOT EXISTS evaluation_runs (run_at TIMESTAMP,
                   version VARCHAR, feature VARCHAR, horizon INTEGER, params VARCHAR,
                   dates INTEGER, mean_ic DOUBLE, ic_t DOUBLE, sharpe DOUBLE,
                   deflated_sharpe DOUBLE, verdict VARCHAR, reasons VARCHAR,
                   ic_series DOUBLE[], ic_dates DATE[])""")
    return con


# ------------------------------------------------------------------------ the matching
def test_an_unmatched_study_is_refused_because_it_measures_momentum():
    con = _db()
    with pytest.raises(EV.EventError, match="measures momentum"):
        EV.event_study(con, event="e_scheme", match=())


def test_the_control_is_drawn_from_the_same_session_and_bucket():
    """Visible in the generated SQL: the cell is (session, bucket) and the effect is the within-cell
    difference, so a control from another day or another momentum decile cannot enter."""
    sql = EV._effect_sql("e_scheme", EV.MATCH_1D)
    assert "PARTITION BY f.business_date" in sql
    assert "GROUP BY j.business_date, b_mom_12_1" in sql
    assert "AVG(c.event_ret - c.control_ret)" in sql


def test_two_matching_dimensions_produce_a_two_dimensional_cell():
    sql = EV._effect_sql("e_scheme", EV.MATCH_2D)
    assert sql.count("NTILE(5)") == 2
    assert "ORDER BY f.mom_12_1" in sql and "ORDER BY f.ret_20d" in sql
    assert "GROUP BY j.business_date, b_mom_12_1, b_ret_20d" in sql


def test_the_bucket_count_shrinks_when_matching_on_more_features():
    """Ten deciles on two dimensions is a hundred cells a session and most would be empty."""
    assert "NTILE(10)" in EV._effect_sql("e_scheme", EV.MATCH_1D)
    assert "NTILE(5)" in EV._effect_sql("e_scheme", EV.MATCH_2D)


def test_an_unknown_event_is_refused_rather_than_interpolated_into_sql():
    con = _db()
    with pytest.raises(EV.EventError, match="unknown flag"):
        EV.event_study(con, event="e_not_a_real_flag")
    with pytest.raises(EV.EventError, match="unknown flag"):
        EV._effect_sql("'; DROP TABLE features; --")


def test_momentum_is_differenced_out_so_a_pure_momentum_effect_reads_as_no_event_effect():
    """The test the whole design exists for. High-momentum names drift up, and the event fires only
    on them. A study that does not match would report the drift as the event's effect."""
    # Every other name in the high-momentum half, so each high decile holds both event names and
    # controls that share its drift. Firing on the whole half leaves no controls and measures nothing.
    con = _db(event_every=10, event_names=range(60, 120, 2), momentum_return=0.004,
              event_return=0.0)
    r = EV.event_study(con, event="e_scheme", horizon=20, min_adv=0.0)
    assert r["mean_effect"] is not None, r["why"]
    assert abs(r["mean_effect"]) < 0.005, (
        f"a pure momentum effect leaked into the event effect: {r['mean_effect']:+.2%}")

    # And the drift is real, so the unmatched comparison would have found "an effect": the event
    # names sit entirely in the top half and their raw excess is positive.
    assert r["mean_event_excess"] > r["mean_control_excess"] - 0.005


def test_a_real_event_effect_is_recovered_with_the_right_sign():
    con = _db(event_every=10, event_names=range(0, 120, 3), event_return=-0.02)
    r = EV.event_study(con, event="e_scheme", horizon=20, min_adv=0.0)
    assert r["mean_effect"] is not None, r["why"]
    assert r["mean_effect"] < -0.005, (
        f"a -2% planted shock came back as {r['mean_effect']:+.2%}")


# ------------------------------------------------------------------------ the statistic
def test_the_t_statistic_is_over_sessions_not_over_events():
    """Events cluster in time - earnings season, budget day, a sector-wide action. Treating 11,931
    order wins as 11,931 independent observations inflates the t by roughly the events per date."""
    con = _db(event_every=5, event_names=range(0, 120, 2), event_return=-0.01)
    r = EV.event_study(con, event="e_scheme", horizon=20, min_adv=0.0)
    assert r["t"] is not None, r["why"]
    assert r["sessions"] < r["cases"], "there are more events than sessions here"
    assert len(r["effects"]) == r["sessions"]
    assert len(r["dates"]) == r["sessions"]
    assert "over sessions" in r["why"]


def test_too_few_matched_sessions_reports_why_instead_of_a_statistic():
    con = _db(event_every=0, event_names=(), event_return=-0.02)
    r = EV.event_study(con, event="e_scheme", horizon=20, min_adv=0.0)
    assert r["mean_effect"] is None and r["t"] is None
    assert "below" in r["why"]


def test_the_study_records_what_it_matched_on():
    con = _db(event_every=10, event_names=range(0, 120, 3), event_return=-0.02)
    r = EV.event_study(con, event="e_scheme", horizon=20, min_adv=0.0, match=EV.MATCH_2D)
    assert r["matched_on"] == ["mom_12_1", "ret_20d"]


# --------------------------------------------------------------------------- the census
def test_the_census_judges_against_the_bonferroni_bar_not_against_two_sigma():
    con = _ledger(_db(event_every=10, event_names=range(0, 120, 3),
                      event_return=-0.02))
    c = EV.census(con, events=("e_scheme", "e_order_win"), horizons=(20,), min_adv=0.0)
    assert c["trials_added"] == 2
    assert c["bar_after"] > c["bar_before"], "running the census raises the bar for everything"
    assert c["bar_after"] > 1.96
    for _e, _h, t in c["clears_the_bar"]:
        assert abs(t) >= c["bar_after"]


def test_the_census_states_how_many_passes_chance_alone_predicts():
    """At nominal significance one in twenty of these passes by construction, so the count means
    nothing without the expectation beside it."""
    con = _ledger(_db(event_every=10, event_names=range(0, 120, 3),
                      event_return=-0.02))
    c = EV.census(con, events=("e_scheme",), horizons=(20,), min_adv=0.0)
    assert "expected_by_chance_at_nominal" in c
    assert c["expected_by_chance_at_nominal"] == pytest.approx(0.05 * len(c["results"]))


def test_buyback_is_excluded_because_it_has_no_rows():
    assert "e_buyback" not in EV.EVENTS


# ------------------------------------------------------------------- the exclusion hook
def test_the_exclusion_keeps_a_flagged_name_out_of_the_book():
    con = _db()
    # Flag the top-ranked name for the whole run.
    for k in range(0, 400, 5):
        vals = ", ".join("TRUE" if c == "e_scheme" else "FALSE" for c in EV.EVENTS)
        con.execute(f"INSERT INTO event_flags VALUES (?, ?, {vals})",
                    [START + timedelta(days=k), "L119"])

    plain = engine._target_book(con, "mom_12_1", START + timedelta(days=200),
                                direction=1, min_adv=0.0, max_positions=5)
    filtered = engine._target_book(con, "mom_12_1", START + timedelta(days=200),
                                   direction=1, min_adv=0.0, max_positions=5,
                                   exclude_events=("e_scheme",))
    assert "L119" in plain
    assert "L119" not in filtered
    assert len(filtered) == 5, "the book is refilled, not left a name short"


def test_the_exclusion_expires_after_its_window():
    con = _db()
    vals = ", ".join("TRUE" if c == "e_scheme" else "FALSE" for c in EV.EVENTS)
    con.execute(f"INSERT INTO event_flags VALUES (?, ?, {vals})",
                [START + timedelta(days=100), "L119"])

    def book(day, window):
        return engine._target_book(con, "mom_12_1", START + timedelta(days=day),
                                   direction=1, min_adv=0.0, max_positions=5,
                                   exclude_events=("e_scheme",),
                                   exclusion_sessions=window)

    assert "L119" not in book(110, 60), "inside the window the name is excluded"
    assert "L119" in book(200, 60), "outside it the name returns"


def test_an_exclusion_only_drops_the_flags_it_was_given():
    con = _db()
    vals = ", ".join("TRUE" if c == "e_order_win" else "FALSE" for c in EV.EVENTS)
    con.execute(f"INSERT INTO event_flags VALUES (?, ?, {vals})",
                [START + timedelta(days=100), "L119"])
    assert "L119" not in engine._target_book(
        con, "mom_12_1", START + timedelta(days=110), direction=1, min_adv=0.0,
        max_positions=5, exclude_events=("e_order_win",))
    assert "L119" in engine._target_book(
        con, "mom_12_1", START + timedelta(days=110), direction=1, min_adv=0.0,
        max_positions=5, exclude_events=("e_scheme",))


def test_the_exclusion_is_part_of_the_experiment_id():
    """Excluding a set of events is a different strategy and a searched choice; an id that collapsed
    them would let the filtered result stand in for the plain one."""
    a = engine.experiment_id({"feature": "mom_12_1", "exclude_events": []})
    b = engine.experiment_id({"feature": "mom_12_1", "exclude_events": ["e_scheme"]})
    assert a != b


def test_a_candle_pattern_is_studied_against_the_candles_table():
    """Candle patterns fire on 0.22% to 12% of rows, so a fifty-name tier holds zero or one hit on a
    typical session and a cross-sectional rank correlation has nothing to correlate. A matched event
    study is the right instrument, and it is the same one the filing events use."""
    assert EV.table_for("e_scheme") == "event_flags"
    assert EV.table_for("k_hammer") == "candles"
    sql = EV._effect_sql("k_hammer", EV.MATCH_2D)
    assert "LEFT JOIN candles v" in sql
    assert "event_flags" not in sql
    assert "v.k_hammer" in sql


def test_every_declared_flag_resolves_to_exactly_one_table():
    seen = {}
    for table, cols in EV.FLAG_TABLES.items():
        for c in cols:
            assert c not in seen, f"{c} is declared in both {seen.get(c)} and {table}"
            seen[c] = table
    for c in EV.EVENTS + EV.CANDLES:
        assert EV.table_for(c) == seen[c]
