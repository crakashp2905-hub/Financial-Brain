"""The compiler, the adversary, the track record and the attribution — each pinned where it lies.

The failure mode these guard against is not a crash. It is a module that produces a plausible
number in a case where it has nothing: a compiler that emits BUY on missing inputs, an adversary
that accepts boilerplate, a track record that weights an ensemble on six observations, an
attribution that explains an unadjusted corporate action as company news.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import duckdb
import pytest

from financial_brain.attribution import move as attrib
from financial_brain.decisions import adversary as adv
from financial_brain.decisions import compile as comp
from financial_brain.decisions import record
from financial_brain.decisions import track_record as tr

AS_OF = date(2026, 6, 15)
GOOD_SCENARIOS = {"bull": {"probability": 0.35, "return": 0.30},
                  "base": {"probability": 0.45, "return": 0.05},
                  "bear": {"probability": 0.20, "return": -0.15}}


def _safe_db():
    """A database where every safety check passes, so the compiler's own rules are what is
    being tested rather than the safety gate in front of them."""
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE adjusted_prices (business_date DATE, isin VARCHAR,
                   lineage VARCHAR, close_adj DOUBLE, turnover DOUBLE, traded_volume BIGINT)""")
    con.execute("""CREATE TABLE announcements (business_date DATE, isin VARCHAR,
                   event_type VARCHAR, materiality VARCHAR, headline VARCHAR,
                   published_at TIMESTAMP)""")
    con.execute("""CREATE TABLE market_regime (business_date DATE, regime VARCHAR)""")
    con.execute("""CREATE TABLE paper_trades (decision_id VARCHAR, isin VARCHAR,
                   status VARCHAR, excess DOUBLE, entry_date DATE, due_date DATE,
                   weight DOUBLE, bucket VARCHAR, exit_date DATE)""")
    con.execute("""CREATE TABLE decisions (decision_id VARCHAR, author VARCHAR)""")
    # safety.check_liquidity reads adv20 through the lineage mapping; without these the
    # fixture fails at the safety gate and never reaches the compiler's own rules.
    con.execute("""CREATE TABLE features (business_date DATE, lineage VARCHAR,
                   adv20 DOUBLE)""")
    con.execute("""CREATE TABLE security_lineage (isin VARCHAR, lineage VARCHAR)""")
    con.execute("INSERT INTO security_lineage VALUES ('INE000TEST01','L1')")
    con.execute("""CREATE TABLE promoter_groups (group_id VARCHAR, anchor VARCHAR,
                   member VARCHAR, company VARCHAR, size INTEGER)""")
    # safety.assess consults the confirmed lessons from closed postmortems.
    con.execute("""CREATE TABLE decision_postmortems (decision_id VARCHAR, excess DOUBLE,
                   regime VARCHAR, prompted_by VARCHAR, bucket VARCHAR, action VARCHAR,
                   invalidation_fired BOOLEAN, features VARCHAR, created_at TIMESTAMP)""")
    for i in range(400):
        d = AS_OF - timedelta(days=i)
        con.execute("INSERT INTO adjusted_prices VALUES (?,?,?,?,?,?)",
                    [d, "INE000TEST01", "L1", 100.0, 5e8, 100000])
        con.execute("INSERT INTO market_regime VALUES (?,?)", [d, "RISK_ON"])
        con.execute("INSERT INTO features VALUES (?,?,?)", [d, "L1", 5e8])
    con.execute("""INSERT INTO announcements VALUES (?,?,?,?,?,?)""",
                [AS_OF, "INE000TEST01", "RESULTS", "high", "Q1 results", None])
    return con


# ------------------------------------------------------------------------ the compiler
def test_claim_aggregation_measures_cancellation_not_variance():
    """Two equally confident opposite claims is total disagreement; two agreeing claims of
    different confidence is none. Variance would call the second case disagreement."""
    opposed = comp.aggregate([comp.Claim("a", 1, 0.8), comp.Claim("b", -1, 0.8)])
    assert opposed["disagreement"] == pytest.approx(1.0)
    agreeing = comp.aggregate([comp.Claim("a", 1, 0.8), comp.Claim("b", 1, 0.2)])
    assert agreeing["disagreement"] == pytest.approx(0.0)


def test_a_strength_outside_zero_to_one_is_refused():
    """Clamping a scale mismatch silently makes it indistinguishable from a real view."""
    with pytest.raises(comp.CompileError):
        comp.aggregate([comp.Claim("a", 1, 87.0)])


def test_missing_scenarios_abstains_rather_than_buying_a_default_size():
    con = _safe_db()
    r = comp.compile_decision(con, isin="INE000TEST01", as_of=AS_OF,
                              claims=[comp.Claim("quant", 1, 0.9)],
                              scenarios=None, entry=100.0, invalidation=90.0,
                              capital_inr=1e7)
    assert r.action == comp.ABSTAIN
    assert r.decided_by == "2.missing_inputs"
    assert any("no scenarios" in x for x in r.reasons)
    assert r.weight == 0.0


def test_no_invalidation_level_abstains_because_nothing_can_be_sized():
    con = _safe_db()
    r = comp.compile_decision(con, isin="INE000TEST01", as_of=AS_OF,
                              claims=[comp.Claim("quant", 1, 0.9)],
                              scenarios=GOOD_SCENARIOS, entry=100.0, invalidation=None,
                              capital_inr=1e7)
    assert r.action == comp.ABSTAIN
    assert r.decided_by == "2.missing_inputs"


def test_negative_expected_value_avoids_not_abstains():
    """The arithmetic was done and came out against. That is a different answer from
    'could not be done', and the decided_by field must say which."""
    con = _safe_db()
    bad = {"up": {"probability": 0.2, "return": 0.05},
           "down": {"probability": 0.8, "return": -0.20}}
    r = comp.compile_decision(con, isin="INE000TEST01", as_of=AS_OF,
                              claims=[comp.Claim("quant", 1, 0.9)],
                              scenarios=bad, entry=100.0, invalidation=90.0,
                              capital_inr=1e7)
    assert r.action == comp.AVOID
    assert r.decided_by == "4.negative_expected_value"


def test_a_positive_but_tiny_edge_watches_rather_than_buys():
    """Eighty-five of this project's rejections were real edges too small to pay their costs.
    'Real and too small' needs its own outcome rather than rounding up to a trade."""
    con = _safe_db()
    tiny = {"up": {"probability": 0.5, "return": 0.008},
            "down": {"probability": 0.5, "return": -0.002}}
    r = comp.compile_decision(con, isin="INE000TEST01", as_of=AS_OF,
                              claims=[comp.Claim("quant", 1, 0.9)],
                              scenarios=tiny, entry=100.0, invalidation=97.0,
                              capital_inr=1e7)
    assert r.action == comp.WATCH
    assert r.decided_by == "7.edge_below_minimum"
    assert r.weight == 0.0


def test_disagreement_watches_rather_than_averaging_into_a_position():
    con = _safe_db()
    r = comp.compile_decision(
        con, isin="INE000TEST01", as_of=AS_OF,
        claims=[comp.Claim("bull", 1, 0.9), comp.Claim("bear", -1, 0.85),
                comp.Claim("quant", 1, 0.2)],
        scenarios=GOOD_SCENARIOS, entry=100.0, invalidation=90.0, capital_inr=1e7)
    assert r.action == comp.WATCH
    assert r.decided_by == "8.sources_disagree"
    assert r.contradictions


def test_a_gate_abstain_propagates_as_abstain_and_a_refuse_as_avoid():
    con = _safe_db()
    common = dict(isin="INE000TEST01", as_of=AS_OF,
                  claims=[comp.Claim("quant", 1, 0.9)], scenarios=GOOD_SCENARIOS,
                  entry=100.0, invalidation=90.0, capital_inr=1e7)
    a = comp.compile_decision(con, gate_decision={"verdict": "ABSTAIN",
                                                  "reasons": ["cov too thin"],
                                                  "approved_weight": 0.0}, **common)
    assert (a.action, a.decided_by) == (comp.ABSTAIN, "5.gate_abstain")
    b = comp.compile_decision(con, gate_decision={"verdict": "REFUSE",
                                                  "reasons": ["book over vol limit"],
                                                  "approved_weight": 0.0}, **common)
    assert (b.action, b.decided_by) == (comp.AVOID, "6.gate_refuse")


def test_a_gate_resize_caps_the_weight_and_says_so():
    con = _safe_db()
    r = comp.compile_decision(con, isin="INE000TEST01", as_of=AS_OF,
                              claims=[comp.Claim("quant", 1, 0.9)],
                              scenarios=GOOD_SCENARIOS, entry=100.0, invalidation=90.0,
                              capital_inr=1e7,
                              gate_decision={"verdict": "RESIZE", "reasons": [],
                                             "approved_weight": 0.012})
    assert r.action == comp.BUY
    assert r.weight == pytest.approx(0.012)
    assert any("resized" in x for x in r.reasons)


def test_every_outcome_names_the_rule_that_produced_it():
    con = _safe_db()
    r = comp.compile_decision(con, isin="INE000TEST01", as_of=AS_OF,
                              claims=[comp.Claim("quant", 1, 0.9)],
                              scenarios=GOOD_SCENARIOS, entry=100.0, invalidation=90.0,
                              capital_inr=1e7)
    assert r.decided_by == "9.passed_every_gate"
    assert r.action == comp.BUY
    assert r.numbers["expected_value"] > 0


# ---------------------------------------------------------------- the no-trade states
def test_abstain_is_an_action_and_no_trade_states_need_no_sizing():
    assert "ABSTAIN" in record.ACTIONS
    assert record.NO_TRADE == {"AVOID", "WATCH", "ABSTAIN", "HOLD"}
    # The point of NO_TRADE: risk review must not demand a weight for a position nobody takes,
    # because that is how ABSTAIN becomes a small BUY.
    assert "BUY" not in record.NO_TRADE


# ------------------------------------------------------------------------ the adversary
@pytest.mark.parametrize("mechanism,observable,evidence,why", [
    ("Macro conditions could deteriorate and broader market volatility could hurt sentiment "
     "while competition could increase.", "Market falls", ["ev1"], "boilerplate"),
    ("Margins may not improve.", "If margin falls below 8%", ["ev1"], "too short"),
    ("Management has a history of over-promising on execution timelines and the order book "
     "quality is likely overstated in their presentations.", "Quality is poor", ["ev1"],
     "unfalsifiable"),
    ("Fixed-price contracts signed before the commodity spike will carry losses as steel "
     "costs pass through with a lag.",
     "If gross margin falls below 6% in the next two filings", [], "uncited"),
])
def test_the_adversary_rejects_the_shapes_of_an_empty_objection(mechanism, observable,
                                                               evidence, why):
    r = adv.review(adv.Challenge(mechanism=mechanism, observable=observable,
                                evidence=evidence),
                   thesis="Order book growth will lift margins as legacy projects roll off.",
                   primary_uncertainty="Whether the new mix carries higher margins.")
    assert not r.passed, f"{why} should not pass"


def test_the_adversary_rejects_a_restatement_of_the_authors_own_uncertainty():
    unc = "Whether the new order mix actually carries higher margins than the legacy book."
    r = adv.review(adv.Challenge(
        mechanism="Whether the new order mix actually carries higher margins than the legacy "
                  "book is unclear and may well not hold.",
        observable="If reported segment margin misses guidance", evidence=["ev1"]),
        thesis="Order book growth will lift margins.", primary_uncertainty=unc)
    assert not r.passed
    assert any("already knew" in f for f in r.failures)


def test_the_adversary_rejects_a_restatement_of_an_invalidation_condition():
    cond = "Order inflow falls below Rs 2000 cr for two consecutive quarters"
    r = adv.review(adv.Challenge(
        mechanism="Order inflow falls below Rs 2000 cr for two consecutive quarters as "
                  "tendering slows down.",
        observable="If quarterly inflow is reported below 2000 cr twice", evidence=["ev1"]),
        thesis="Order book growth will lift margins.",
        primary_uncertainty="Whether the mix carries higher margins.",
        invalidation_conditions=[cond])
    assert not r.passed
    assert any("already commits" in f for f in r.failures)


def test_a_real_objection_passes():
    r = adv.review(adv.Challenge(
        mechanism="Fixed-price transmission contracts signed pre-2025 embed steel at lower "
                  "cost, so the margin lift attributed to new orders is actually a one-off "
                  "inventory gain that reverses once older inventory is consumed.",
        observable="If inventory days fall below 45 while gross margin compresses in the next "
                   "two quarterly filings, the lift was inventory and not mix",
        evidence=["ev_fin_2026Q1", "ev_inv_note"], probability=0.35),
        thesis="Order book growth will lift margins as legacy projects roll off.",
        primary_uncertainty="Whether the new mix carries higher margins.")
    assert r.passed, r.failures


# --------------------------------------------------------------------- the track record
def test_brier_decomposition_separates_calibrated_from_informative():
    """A source that always states the base rate is perfectly calibrated and carries no
    information. Only resolution says whether it distinguishes anything."""
    always_base = tr.brier([(0.5, 1)] * 50 + [(0.5, 0)] * 50)
    assert always_base["resolution"] == pytest.approx(0.0, abs=1e-9)
    assert always_base["reliability"] == pytest.approx(0.0, abs=1e-9)
    assert always_base["skill_score"] == pytest.approx(0.0, abs=1e-9)
    sharp = tr.brier([(0.9, 1)] * 50 + [(0.1, 0)] * 50)
    assert sharp["resolution"] > 0.2
    assert sharp["skill_score"] > 0.5


def test_a_source_worse_than_its_base_rate_is_reported_as_such():
    bad = tr.brier([(0.9, 0)] * 40 + [(0.1, 1)] * 40)
    assert bad["beats_base_rate"] is False
    assert bad["skill_score"] < 0


def test_weights_stay_equal_until_the_record_can_support_otherwise():
    """Weighting an ensemble on six observations converts noise into confidence."""
    con = duckdb.connect(":memory:")
    tr.ensure_table(con)
    tr.record(con, [tr.Outcome(f"c{i}", "hot_source", 1, 0.9, 0.05) for i in range(6)]
              + [tr.Outcome(f"d{i}", "cold_source", 1, 0.9, -0.05) for i in range(6)])
    w = tr.weights(con)
    assert w["equal"] is True
    assert set(w["weights"]) == {"hot_source", "cold_source"}
    assert w["weights"]["hot_source"] == pytest.approx(0.5)
    assert "observations that beat its base rate" in w["why"]


def test_a_cell_below_the_minimum_reports_its_numbers_and_is_not_usable():
    con = duckdb.connect(":memory:")
    tr.ensure_table(con)
    tr.record(con, [tr.Outcome(f"c{i}", "s", 1, 0.8, 0.02) for i in range(6)])
    cells = tr.score(con)
    assert cells[0]["n"] == 6
    assert cells[0]["usable"] is False
    assert "converts noise into confidence" in cells[0]["why_not_usable"]


def test_a_neutral_claim_is_right_only_when_the_move_was_small():
    assert tr.Outcome("c", "s", 0, 0.5, 0.005).correct == 1
    assert tr.Outcome("c", "s", 0, 0.5, 0.11).correct == 0


# ---------------------------------------------------------------------- the attribution
def test_a_move_on_a_circuit_band_is_flagged_as_capped():
    for band in attrib.CIRCUIT_BANDS:
        assert any(abs(abs(band) - b) <= attrib.CIRCUIT_TOLERANCE
                   for b in attrib.CIRCUIT_BANDS)
    assert attrib.IMPLAUSIBLE_SIGMA > attrib.NEWS_HUNT_SIGMA


def test_the_two_slope_regression_recovers_planted_coefficients():
    import random
    rng = random.Random(8)
    x1 = [rng.gauss(0, 0.01) for _ in range(300)]
    x2 = [rng.gauss(0, 0.01) for _ in range(300)]
    y = [0.0004 + 1.3 * a + 0.7 * b + rng.gauss(0, 0.001)
         for a, b in zip(x1, x2)]
    a0, b1, b2 = attrib._ols2(y, x1, x2)
    assert b1 == pytest.approx(1.3, abs=0.05)
    assert b2 == pytest.approx(0.7, abs=0.05)
    assert a0 == pytest.approx(0.0004, abs=0.0004)


def test_collinear_regressors_fall_back_rather_than_dividing_by_zero():
    y = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    x = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    a0, b1, b2 = attrib._ols2(y, x, x)
    assert b2 == 0.0
    assert math.isfinite(a0) and math.isfinite(b1)
