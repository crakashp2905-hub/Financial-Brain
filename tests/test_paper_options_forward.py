from datetime import date, datetime, timedelta, timezone

import duckdb
import pytest

from financial_brain.ingest.options_quotes import SCHEMA as QUOTE_SCHEMA
from financial_brain.paper.options import OptionPaperError
from financial_brain.paper.options_forward import SCHEMA, audit, record_intent, record_outcome
from financial_brain.paper.options_runner import Intent


IST = timezone(timedelta(hours=5, minutes=30))
DECIDED = datetime(2026, 10, 29, 10, tzinfo=IST)


def intent():
    return Intent("forward-1", "cohort-a", "v1", 123, DECIDED,
                  DECIDED + timedelta(minutes=5), 110.0)


def ledgers():
    paper, quotes = duckdb.connect(), duckdb.connect()
    paper.execute(SCHEMA)
    quotes.execute(QUOTE_SCHEMA)
    return paper, quotes


def test_intent_is_append_only_and_rejects_late_registration():
    paper, _ = ledgers()
    with pytest.raises(OptionPaperError, match="before its entry window"):
        record_intent(paper, intent(), now=DECIDED + timedelta(minutes=2))
    digest = record_intent(paper, intent(), now=DECIDED + timedelta(seconds=1))
    assert len(digest) == 64
    with pytest.raises(OptionPaperError, match="already exists"):
        record_intent(paper, intent(), now=DECIDED + timedelta(seconds=2))
    assert audit(paper)["pending"] == 1


def test_forward_outcome_preserves_missing_quote_and_is_immutable():
    paper, quotes = ledgers()
    i = intent()
    record_intent(paper, i, now=DECIDED + timedelta(seconds=1))
    with pytest.raises(OptionPaperError, match="before the exit window"):
        record_outcome(paper, quotes, i, now=DECIDED + timedelta(minutes=5))
    result = record_outcome(paper, quotes, i, now=DECIDED + timedelta(minutes=7))
    assert result["status"] == "NO_ENTRY_QUOTE"
    with pytest.raises(OptionPaperError, match="already recorded"):
        record_outcome(paper, quotes, i, now=DECIDED + timedelta(minutes=8))
    assert audit(paper)["outcomes"] == 1
    paper.execute("UPDATE outcomes SET result = '{}' WHERE signal_id = 'forward-1'")
    assert audit(paper)["invalid_outcomes"] == [("cohort-a", "v1", "forward-1")]


def test_forward_fill_needs_observed_fresh_quotes_after_registration():
    paper, quotes = ledgers()
    i = intent()
    record_intent(paper, i, now=DECIDED + timedelta(seconds=1))
    for received, bid in ((DECIDED + timedelta(seconds=10), 100.0),
                          (DECIDED + timedelta(minutes=5), 115.0)):
        quotes.execute("""INSERT INTO option_quotes (received_at, source_at, instrument_token, tradingsymbol,
                       underlying, expiry, strike, option_type, lot_size, bid, ask, bid_qty, ask_qty, book_quality)
                       VALUES (?, ?, 123, 'NIFTY26NOV25000CE', 'NIFTY', ?, 25000, 'CE', 65, ?, ?, 1000, 1000,
                       'TWO_SIDED')""", [received, received - timedelta(seconds=1), date(2026, 11, 5), bid, bid + 1])
    result = record_outcome(paper, quotes, i, now=DECIDED + timedelta(minutes=7))
    assert result["status"] == "CLOSED" and result["net_pnl_inr"] > 0
    assert audit(paper)["external_timestamp_attested"] is False
