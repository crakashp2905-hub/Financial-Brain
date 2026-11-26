from datetime import date, datetime, timedelta, timezone

import duckdb

from financial_brain.ingest.options_quotes import SCHEMA
from financial_brain.paper.options import Quote
from financial_brain.paper.options_runner import Intent, archive_quotes, replay_cohort, run_intent
from financial_brain.kernel.units import OptionLots


IST = timezone(timedelta(hours=5, minutes=30))
D = datetime(2026, 10, 29, 10, tzinfo=IST)
EXPIRY = date(2026, 11, 5)


def intent(expected=110.0):
    return Intent("signal-1", "option-test", "v1", 123, D,
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


def test_options_runner_skips_cost_dominated_or_thin_entry():
    entry = quote(D + timedelta(seconds=10))
    assert run_intent(intent(expected=101.01), [entry])["status"] == "SKIPPED_COST_OR_LIQUIDITY"
    thin = run_intent(intent(), [quote(D + timedelta(seconds=10), ask_qty=10)])
    assert thin["status"] == "NO_FILL"
    assert thin["reason"] == "INSUFFICIENT_VISIBLE_DEPTH"


def test_option_intent_normalizes_nominal_lots_before_replay():
    base = intent()
    typed = Intent(base.signal_id, base.cohort, base.rule_version, base.token, base.decided_at,
                   base.planned_exit_at, base.expected_exit_bid, lots=OptionLots(2))
    assert typed.lots == 2


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
