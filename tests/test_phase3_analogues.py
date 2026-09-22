"""Historical analogues: scenarios from the record, not from a model's imagination.

Three things are worth locking here, and only one of them is arithmetic:

* the terciles reproduce the sample (their probability-weighted mean *is* the mean excess),
  so an EV built from them cannot flatter the history it came from;
* a thin sample yields **no** scenarios rather than noisy ones, which makes the decision
  fail its arithmetic and become a NO TRADE;
* nothing after ``as_of`` can enter the sample - the failure that would make every
  backtest downstream of this meaningless.
"""
from __future__ import annotations

from datetime import date

import duckdb
import pytest

from financial_brain.decisions import expected_value as ev
from financial_brain.features import analogues as A


def _con(events, *, days=400, n_names=40):
    """A toy market: n_names securities on consecutive dates, plus the given events.

    Built with ``generate_series`` rather than row-by-row inserts - DuckDB executes
    individual INSERTs slowly enough that a 16,000-row fixture takes minutes.
    """
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE announcements (isin VARCHAR, business_date DATE,
                   event_type VARCHAR, materiality VARCHAR)""")
    con.execute("""CREATE TABLE adjusted_prices AS
                   SELECT 'IN' || lpad(i::VARCHAR, 4, '0') AS isin,
                          (DATE '2020-01-01' + to_days(d::INTEGER))::DATE AS business_date,
                          100.0                            AS close_adj,
                          1e9                              AS turnover
                   FROM generate_series(0, ? - 1) t(i),
                        generate_series(0, ? - 1) u(d)""", [n_names, days])
    con.executemany("INSERT INTO announcements VALUES (?,?,?,?)", events)
    return con, date(2020, 1, 1)


def test_a_thin_sample_yields_no_scenarios_at_all():
    """Below MIN_CASES the answer is None, not a distribution with wide error bars."""
    events = [(f"IN{i:04d}", date(2020, 2, 1), "RARE", "high") for i in range(5)]
    con, _ = _con(events)
    assert A.scenarios(con, "RARE", date(2022, 1, 1)) is None
    assert A.summary(con, "RARE", date(2022, 1, 1))["usable"] is False


def test_no_case_may_resolve_after_the_as_of_date():
    """PIT: an event whose 90-day horizon has not closed by as_of cannot be counted."""
    events = [(f"IN{i:04d}", date(2020, 2, 1), "E", "high") for i in range(40)]
    con, _ = _con(events)
    # the horizon closes ~2020-05-01; as_of before that must see nothing
    assert A.outcomes(con, "E", date(2020, 4, 1)) == []
    assert A.outcomes(con, "E", date(2020, 12, 1)) != []


def test_only_events_inside_the_lookback_window_are_counted():
    events = [(f"IN{i:04d}", date(2020, 2, 1), "E", "high") for i in range(40)]
    con, _ = _con(events)
    # the event is 2020-02-01; a one-year window from 2020-12-01 reaches it, a
    # zero-year window starts at as_of and cannot
    assert A.outcomes(con, "E", date(2020, 12, 1), lookback_years=1) != []
    assert A.outcomes(con, "E", date(2020, 12, 1), lookback_years=0) == []


def test_illiquid_names_are_excluded_by_the_turnover_floor():
    events = [(f"IN{i:04d}", date(2020, 2, 1), "E", "high") for i in range(40)]
    con, _ = _con(events)
    con.execute("UPDATE adjusted_prices SET turnover = 1000")
    assert A.outcomes(con, "E", date(2020, 12, 1)) == []


def test_terciles_reproduce_the_sample_mean():
    """The EV of the scenario set equals the mean excess of the cases behind it.

    This is the property that keeps the arithmetic honest: the distribution handed to a
    decision cannot be more optimistic than the history it was built from.
    """
    sample = [(-10 + i) / 100 for i in range(60)]     # -10% .. +49%, 60 cases
    sc = A.terciles(sample)
    got = sum(b["probability"] * b["return"] for b in sc.values())
    assert got == pytest.approx(sum(sample) / len(sample), abs=1e-3)
    assert sum(b["cases"] for b in sc.values()) == len(sample)


def test_the_scenario_set_is_one_the_expected_value_module_accepts():
    """The handoff: whatever analogues produce, ev.assess must be able to score."""
    sample = [(-10 + i) / 100 for i in range(60)]
    assessment = ev.assess(A.terciles(sample))
    assert assessment.positive()
    assert assessment.worst < 0            # a loss case is always present in real data


def test_a_sample_with_no_losing_case_is_refused_downstream():
    """A distribution where nothing loses money fails ev.assess by design."""
    sc = A.terciles([0.01 + i / 1000 for i in range(60)])
    with pytest.raises(ev.EVError, match="no scenario loses money"):
        ev.assess(sc)


def test_probabilities_stay_within_tolerance_for_samples_not_divisible_by_three():
    for n in (31, 32, 40, 101):
        sc = A.terciles([(i - n // 2) / 100 for i in range(n)])
        total = sum(b["probability"] for b in sc.values())
        assert abs(total - 1.0) <= ev.TOLERANCE, n
        ev.assess(sc)                       # must not raise


# --- the committee's arithmetic ----------------------------------------------

class _Doss:
    """Only the two attributes ``_arithmetic`` reads."""
    def __init__(self, isin, as_of):
        self.isin, self.as_of = isin, as_of


def _committee_con():
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE announcements (isin VARCHAR, business_date DATE,
                   event_type VARCHAR, materiality VARCHAR)""")
    con.execute(A.CACHE_DDL)
    return con


def _cache(con, event_type, as_of_month, scenarios):
    import json
    con.execute("INSERT INTO analogue_distributions VALUES (?,?,?,?,?,?,?)",
                [event_type, as_of_month, 90, 900, 0.02, json.dumps(scenarios), None])


CHECKS = [{"check": "drawdown_from", "reference": 500.0, "pct": 0.15},
          {"check": "adverse_tone"}]
BANDS = {"bear": {"probability": 0.333, "return": -0.18},
         "base": {"probability": 0.334, "return": 0.01},
         "bull": {"probability": 0.333, "return": 0.25}}


def test_sizing_comes_from_the_invalidation_distance_not_a_flat_weight():
    """The finding that prompted this: 25 decisions sized at a flat 3% with nothing
    behind the number. The entry and invalidation now come from the drawdown check that
    is already the thesis's stated way of being wrong."""
    from financial_brain.committee import run as cr
    con = _committee_con()
    _, sizing = cr._arithmetic(con, _Doss("IN0", date(2026, 6, 1)), CHECKS)
    assert sizing["entry"] == 500.0
    assert sizing["invalidation"] == pytest.approx(425.0)      # 500 x (1 - 0.15)
    # and the result is a size the EV module accepts and can act on
    got = ev.size(portfolio_inr=1_000_000.0, entry=sizing["entry"],
                  invalidation=sizing["invalidation"],
                  risk_budget=sizing["risk_budget"])
    assert got["shares"] == 66                                 # 5,000 / 75 per share
    assert got["rupees_at_risk"] <= 5_000.0


def test_no_drawdown_check_means_no_sizing_at_all():
    from financial_brain.committee import run as cr
    con = _committee_con()
    _, sizing = cr._arithmetic(con, _Doss("IN0", date(2026, 6, 1)),
                               [{"check": "adverse_tone"}])
    assert sizing == {}


def test_scenarios_are_read_from_the_cache_for_a_live_event():
    from financial_brain.committee import run as cr
    con = _committee_con()
    con.execute("INSERT INTO announcements VALUES ('IN0', DATE '2026-05-20', 'ORDER_WIN', 'high')")
    _cache(con, "ORDER_WIN", date(2026, 6, 1), BANDS)
    scenarios, sizing = cr._arithmetic(con, _Doss("IN0", date(2026, 6, 1)), CHECKS)
    assert ev.assess(scenarios).positive()
    assert sizing["analogue"]["event_type"] == "ORDER_WIN"


def test_a_stale_event_does_not_lend_its_distribution():
    """A high-materiality filing from three years ago is not this thesis, and borrowing
    its distribution would be the same mistake as borrowing a stale calibration."""
    from financial_brain.committee import run as cr
    con = _committee_con()
    con.execute("INSERT INTO announcements VALUES ('IN0', DATE '2023-01-10', 'ORDER_WIN', 'high')")
    _cache(con, "ORDER_WIN", date(2026, 6, 1), BANDS)
    scenarios, _ = cr._arithmetic(con, _Doss("IN0", date(2026, 6, 1)), CHECKS)
    assert scenarios == {}


def test_an_uncached_event_type_yields_no_scenarios_rather_than_a_slow_scan():
    """The committee never builds a distribution inline: an uncached type is a NO TRADE
    until `fb analogues --warm` has run, not a sixty-second stall in the middle of a run."""
    from financial_brain.committee import run as cr
    con = _committee_con()
    con.execute("INSERT INTO announcements VALUES ('IN0', DATE '2026-05-20', 'RARE', 'high')")
    scenarios, _ = cr._arithmetic(con, _Doss("IN0", date(2026, 6, 1)), CHECKS)
    assert scenarios == {}


def test_routine_filings_do_not_enter_a_high_materiality_distribution():
    """The sample has to match the population the decision is drawn from.

    The committee only ever looks up a distribution for a *high*-materiality filing. A
    distribution built over every routine compliance notice of the same type would
    describe a different population than the trade it is sizing - and would be dominated
    by it, since routine filings outnumber material ones by orders of magnitude.
    """
    routine = [(f"IN{i:04d}", date(2020, 2, 1), "E", "low") for i in range(40)]
    con, _ = _con(routine)
    assert A.outcomes(con, "E", date(2020, 12, 1)) == []
    assert A.outcomes(con, "E", date(2020, 12, 1), materiality="low") != []
