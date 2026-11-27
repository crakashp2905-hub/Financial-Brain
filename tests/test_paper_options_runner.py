from datetime import date, datetime, timedelta, timezone
from dataclasses import replace

import duckdb

from financial_brain.ingest.options_quotes import SCHEMA
from financial_brain.paper.options import Quote
from financial_brain.paper.options_runner import (
    DirectionalOutcome,
    Intent,
    archive_quotes,
    replay_cohort,
    run_intent,
    score_direction_separately,
)
from financial_brain.kernel.units import OptionLots


IST = timezone(timedelta(hours=5, minutes=30))
D = datetime(2026, 10, 29, 10, tzinfo=IST)
EXPIRY = date(2026, 11, 5)


def intent(expected=110.0):
    return Intent("option-signal-1", "direction-signal-1", "a" * 64, "b" * 64,
                  "option-test", "v1", 123, D,
                  D + timedelta(minutes=5), expected)


def quote(at, bid=100.0, ask=101.0, bid_qty=1000, ask_qty=1000):
    return Quote(123, "NIFTY26NOV25000CE", EXPIRY, 65, at, bid, ask, bid_qty, ask_qty)


def test_options_runner_requires_later_entry_and_real_exit():
    i = intent()
    assert run_intent(i, [quote(D)])["status"] == "NO_ENTRY_QUOTE"
    entry = quote(D + timedelta(seconds=10))
    assert run_intent(i, [entry])["status"] == "NO_EXIT_QUOTE"
    done = run_intent(i, [entry, quote(D + timedelta(minutes=5), bid=115, ask=116)])
    assert done["status"] == "CLOSED" and done["net_pnl_inr"] > 0
    assert done["mode"] == "replay" and done["rule_version"] == "v1"
    assert done["evidence_class"] == "HISTORICAL_REPLAY" and done["independently_attested"] is False
    assert done["signal_id"] == done["option_signal_id"] == "option-signal-1"
    assert done["directional_signal_id"] == "direction-signal-1"


def test_options_runner_skips_cost_dominated_or_thin_entry():
    entry = quote(D + timedelta(seconds=10))
    assert run_intent(intent(expected=101.01), [entry])["status"] == "SKIPPED_COST_OR_LIQUIDITY"
    thin = run_intent(intent(), [quote(D + timedelta(seconds=10), ask_qty=10)])
    assert thin["status"] == "NO_FILL"
    assert thin["reason"] == "INSUFFICIENT_VISIBLE_DEPTH"


def test_option_intent_normalizes_nominal_lots_before_replay():
    base = intent()
    typed = Intent(base.signal_id, base.directional_signal_id,
                   base.directional_evidence_sha256, base.option_evidence_sha256,
                   base.cohort, base.rule_version, base.token, base.decided_at,
                   base.planned_exit_at, base.expected_exit_bid, lots=OptionLots(2))
    assert typed.lots == 2


def test_option_and_direction_hypotheses_require_distinct_ids_and_evidence():
    base = intent()
    import pytest
    from financial_brain.paper.options import OptionPaperError

    with pytest.raises(OptionPaperError, match="distinct signal IDs"):
        Intent(base.signal_id, base.signal_id, base.directional_evidence_sha256,
               base.option_evidence_sha256, base.cohort, base.rule_version, base.token,
               base.decided_at, base.planned_exit_at, base.expected_exit_bid).validate()
    with pytest.raises(OptionPaperError, match="SHA-256"):
        Intent(base.signal_id, base.directional_signal_id, "bad", base.option_evidence_sha256,
               base.cohort, base.rule_version, base.token, base.decided_at,
               base.planned_exit_at, base.expected_exit_bid).validate()


def test_stale_and_zero_depth_option_quotes_are_no_fill():
    i = intent()
    stale = quote(D + timedelta(seconds=45))
    assert run_intent(i, [stale])["status"] == "NO_FILL"
    assert run_intent(i, [stale])["reason"] == "STALE_QUOTE"
    zero = quote(D + timedelta(seconds=10), bid_qty=0)
    assert run_intent(i, [zero])["status"] == "NO_FILL"
    assert run_intent(i, [zero])["reason"] == "ZERO_VISIBLE_DEPTH"


def test_stale_and_zero_depth_exit_quotes_never_realize_a_win():
    i = intent()
    entry = quote(D + timedelta(seconds=10))
    stale_exit_time = i.planned_exit_at + timedelta(seconds=45)
    stale_exit = quote(stale_exit_time, bid=120)
    stale_result = run_intent(i, [entry, stale_exit])
    assert stale_result["status"] == "NO_FILL"
    assert stale_result["reason"] == "STALE_QUOTE"
    zero_exit = quote(i.planned_exit_at, bid=120, bid_qty=0)
    zero_result = run_intent(i, [entry, zero_exit])
    assert zero_result["status"] == "NO_FILL"
    assert zero_result["reason"] == "ZERO_VISIBLE_DEPTH"


def test_archive_requires_fresh_broker_timestamp():
    con = duckdb.connect()
    con.execute(SCHEMA)
    fresh = D + timedelta(seconds=10)
    stale = D + timedelta(minutes=5)
    for received, source, bid in ((fresh, fresh - timedelta(seconds=2), 100.0),
                                  (stale, stale - timedelta(minutes=2), 115.0)):
        con.execute("""INSERT INTO option_quotes (received_at, source_at, instrument_token, tradingsymbol,
                       underlying, expiry, strike, option_type, lot_size, bid, ask, bid_qty, ask_qty, book_quality)
                       VALUES (?, ?, 123, 'NIFTY26NOV25000CE', 'NIFTY', ?, 25000, 'CE', 65, ?, ?, 1000, 1000,
                       'TWO_SIDED')""", [received, source, EXPIRY, bid, bid + 1])
    got = archive_quotes(con, intent())
    assert len(got) == 1 and got[0].observed_at == fresh
    assert run_intent(intent(), got)["status"] == "NO_EXIT_QUOTE"
    report = replay_cohort(con, [intent()])
    assert report["evidence_class"] == "HISTORICAL_REPLAY" and report["independently_attested"] is False
    assert report["coverage"] == 0 and report["status_counts"] == {"NO_EXIT_QUOTE": 1}
    assert report["net_pnl_inr"] == 0
    assert report["option_attribution"][0]["no_fill_or_unresolved"] == 1
    assert report["direction_correctness"] == "NOT_COMPUTED_UNDERLYING_OUTCOME_JOIN_REQUIRED"


def test_closed_replay_attributes_net_pnl_by_setup_expiry_delta_and_spread():
    con = duckdb.connect()
    con.execute(SCHEMA)
    i = replace(intent(), option_setup_id="momentum-breakout", delta_at_decision=0.42)
    entry_at = D + timedelta(seconds=10)
    exit_at = i.planned_exit_at
    for received, source, bid, ask in (
            (entry_at, entry_at - timedelta(milliseconds=100), 100.0, 101.0),
            (exit_at, exit_at - timedelta(milliseconds=100), 115.0, 116.0)):
        con.execute("""INSERT INTO option_quotes
            (received_at, source_at, instrument_token, tradingsymbol, underlying, expiry,
             strike, option_type, lot_size, bid, ask, bid_qty, ask_qty, book_quality)
            VALUES (?, ?, 123, 'NIFTY26NOV25000CE', 'NIFTY', ?, 25000, 'CE', 65,
                    ?, ?, 1000, 1000, 'TWO_SIDED')""",
                    [received, source, EXPIRY, bid, ask])
    report = replay_cohort(con, [i])
    group = report["option_attribution"][0]
    assert (group["option_type"], group["expiry_bucket"], group["delta_bucket"],
            group["spread_bucket"]) == ("CE", "2-7D", "0.25-0.50", "25-100_BPS")
    assert group["closed"] == 1 and group["intents"] == 1
    assert group["net_pnl_inr"] == report["net_pnl_inr"]


def test_direction_accuracy_is_scored_separately_from_option_premium_profit():
    con = duckdb.connect()
    con.execute(SCHEMA)
    i = replace(intent(), directional_prediction="DOWN", underlying_price_at_decision=25000,
                flat_tolerance_bps=5)
    entry_at, exit_at = D + timedelta(seconds=10), i.planned_exit_at
    for received, source, bid, ask in (
            (entry_at, entry_at - timedelta(milliseconds=100), 100.0, 101.0),
            (exit_at, exit_at - timedelta(milliseconds=100), 95.0, 96.0)):
        con.execute("""INSERT INTO option_quotes
            (received_at, source_at, instrument_token, tradingsymbol, underlying, expiry,
             strike, option_type, lot_size, bid, ask, bid_qty, ask_qty, book_quality)
            VALUES (?, ?, 123, 'NIFTY26NOV25000CE', 'NIFTY', ?, 25000, 'CE', 65,
                    ?, ?, 1000, 1000, 'TWO_SIDED')""",
                    [received, source, EXPIRY, bid, ask])
    outcome = DirectionalOutcome(
        directional_signal_id=i.directional_signal_id, observed_at=exit_at,
        outcome_underlying_price=24900, source_sha256="c" * 64)
    report = replay_cohort(con, [i], directional_outcomes={i.directional_signal_id: outcome})
    score = report["results"][0]["directional_score"]
    assert score["directional_correct"] is True
    assert score["option_premium_profitable"] is False
    assert score["directional_outcome_source_status"] == "CALLER_SUPPLIED_UNVERIFIED"
    assert report["direction_metrics"]["directional_accuracy"] == 1.0
    assert report["direction_metrics"]["closed_option_premium_profitable_rate"] == 0.0
    assert report["direction_metrics"]["independently_attested"] is False


def test_direction_outcome_requires_future_observation_and_valid_evidence_reference():
    bad = DirectionalOutcome("direction-1", D, 101, "c" * 64)
    import pytest
    from financial_brain.paper.options import OptionPaperError

    with pytest.raises(OptionPaperError, match="later outcome"):
        score_direction_separately(bad, predicted_direction="UP", decision_at=D,
                                   decision_underlying_price=100, flat_tolerance_bps=0,
                                   option_net_pnl_inr=1.0)
