from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.paper.options import Quote
from financial_brain.paper.options_spread import (
    Leg,
    OptionLegEvidence,
    OptionPaperError,
    prepare_debit_vertical,
)


NOW = datetime(2026, 11, 13, 10, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def _evidence(token: int, strike: int, bid: float, ask: float, *,
              observed_at: datetime = NOW - timedelta(seconds=1),
              available_at: datetime = NOW - timedelta(milliseconds=200),
              venue: str = "NFO", master_venue: str = "NFO",
              expiry: date = date(2026, 11, 26), master_expiry: date | None = None,
              lot: int = 65, master_lot: int | None = None,
              quote_hash: str = HASH_B) -> OptionLegEvidence:
    quote = Quote(token, f"NIFTY-{expiry.isoformat()}-{strike}-CE", expiry, lot,
                  observed_at, bid, ask, 500, 500)
    leg = Leg(quote, "NIFTY", "CE", strike)
    return OptionLegEvidence(
        leg=leg,
        quote_venue=venue,
        master_venue=master_venue,
        quote_available_at=available_at,
        quote_source_sha256=quote_hash,
        contract_master_sha256=HASH_C,
        contract_master_expiry=master_expiry or expiry,
        contract_master_lot_size=master_lot or lot,
    )


def _prepare(long=None, short=None, **kwargs):
    return prepare_debit_vertical(
        long or _evidence(1, 25000, 100, 101),
        short or _evidence(2, 25100, 40, 41),
        decision_at=kwargs.pop("decision_at", NOW),
        expected_venue=kwargs.pop("expected_venue", "NFO"),
        **kwargs,
    )


def test_each_fresh_leg_quote_is_bound_to_venue_contract_terms_and_source_hashes():
    result = _prepare()
    assert result["status"] == "ELIGIBLE_PAPER_OPTION_SPREAD"
    assert result["allow_execution"] is True
    assert len(result["leg_evidence"]) == 2
    assert result["economics"]["expiry"] == date(2026, 11, 26)
    assert len(result["evidence_sha256"]) == 64


@pytest.mark.parametrize("overrides, expected", [
    ({"observed_at": NOW - timedelta(seconds=10)}, "LONG_QUOTE_STALE"),
    ({"available_at": NOW + timedelta(seconds=1)}, "LONG_QUOTE_NOT_AVAILABLE_AT_DECISION"),
    ({"venue": "BSE"}, "LONG_VENUE_MISMATCH"),
    ({"master_lot": 50}, "LONG_CONTRACT_TERMS_MISMATCH"),
    ({"master_expiry": date(2026, 12, 31)}, "LONG_CONTRACT_TERMS_MISMATCH"),
    ({"quote_hash": ""}, "LONG_MISSING_QUOTE_SHA256"),
])
def test_any_ineligible_leg_blocks_the_vertical(overrides, expected):
    long = _evidence(1, 25000, 100, 101, **overrides)
    result = _prepare(long=long)
    assert result["allow_execution"] is False
    assert expected in result["blockers"]
    assert "economics" not in result


def test_unavailable_or_naive_decision_timestamp_is_rejected():
    with pytest.raises(OptionPaperError, match="timezone-aware"):
        _prepare(decision_at=datetime(2026, 11, 13, 10))


def test_expired_and_duplicate_option_contracts_do_not_pass():
    expired = _evidence(1, 25000, 100, 101, expiry=date(2026, 11, 13))
    assert not _prepare(long=expired)["allow_execution"]
    same = _evidence(1, 25100, 40, 41)
    result = _prepare(long=same, short=same)
    assert not result["allow_execution"]
    assert "DUPLICATE_OPTION_INSTRUMENT" in result["blockers"]
