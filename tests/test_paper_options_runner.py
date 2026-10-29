from datetime import date, datetime, timedelta, timezone

import duckdb

from financial_brain.ingest.options_quotes import SCHEMA
from financial_brain.paper.options import Quote
from financial_brain.paper.options_runner import Intent, archive_quotes, replay_cohort, run_intent


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


def test_options_runner_skips_cost_dominated_or_thin_entry():
    entry = quote(D + timedelta(seconds=10))
    assert run_intent(intent(expected=101.01), [entry])["status"] == "SKIPPED_COST_OR_LIQUIDITY"
    assert run_intent(intent(), [quote(D + timedelta(seconds=10), ask_qty=10)])[
        "status"] == "SKIPPED_COST_OR_LIQUIDITY"


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
    assert report["coverage"] == 0 and report["status_counts"] == {"NO_EXIT_QUOTE": 1}
    assert report["net_pnl_inr"] == 0
