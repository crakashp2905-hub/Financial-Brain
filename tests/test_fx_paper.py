from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest

from financial_brain.kernel.fx_paper import (
    FxContractSpec,
    FxExposureEvidence,
    FxPaperError,
    FxQuote,
    FxVenueAuthorization,
    PAIRS,
    PRODUCTS,
    SUPPORTED_VENUES,
    rbi_rulebook_sha256,
    simulate_fx_paper_intent,
)


AS_OF = datetime(2026, 11, 14, 5, 0, tzinfo=timezone.utc)
DAY = AS_OF.date()
HASH_A = "a" * 64
HASH_B = "b" * 64
UNSET = object()


def contract(**overrides):
    values = {
        "contract_id": "USDINR-FUT-NOV26", "jurisdiction": "IN", "venue_id": "NSE",
        "pair": "USD-INR", "product": "FUTURE", "expiry": date(2026, 11, 25),
        "effective_from": date(2026, 11, 1), "effective_through": date(2026, 11, 30),
        "lot_size": 1, "units_per_lot": Decimal("1000"), "settlement_currency": "INR",
        "underlying_kind": "FUTURE", "exercise_style": "NONE",
        "source_id": "synthetic-contract-master", "source_sha256": HASH_A,
    }
    values.update(overrides)
    return FxContractSpec(**values)


def venue(**overrides):
    values = {
        "venue_id": "NSE", "jurisdiction": "IN", "venue_kind": "RECOGNIZED_STOCK_EXCHANGE",
        "authorized": True, "effective_from": date(2026, 1, 1),
        "effective_through": date(2026, 12, 31), "checked_at": AS_OF - timedelta(days=1),
        "source_id": "synthetic-authorization-register", "source_sha256": HASH_B,
    }
    values.update(overrides)
    return FxVenueAuthorization(**values)


def quote(**overrides):
    values = {
        "bid": Decimal("83.10"), "ask": Decimal("83.12"),
        "observed_at": AS_OF - timedelta(seconds=2),
        "available_at": AS_OF - timedelta(seconds=1),
        "source_id": "synthetic-two-sided-quote", "source_sha256": HASH_A,
    }
    values.update(overrides)
    return FxQuote(**values)


def exposure(**overrides):
    values = {
        "pair": "USD-INR", "units": Decimal("1000"), "exposure_id": "contracted-invoice-1",
        "contracted": True, "unhedged": True, "valid_on": DAY,
        "source_id": "synthetic-exposure-ledger", "source_sha256": HASH_B,
    }
    values.update(overrides)
    return FxExposureEvidence(**values)


def simulate(*, spec=None, venue_data=UNSET, market_quote=UNSET, exposure_data=UNSET):
    return simulate_fx_paper_intent(
        spec or contract(),
        venue_authorization=venue() if venue_data is UNSET else venue_data,
        quote=quote() if market_quote is UNSET else market_quote,
        exposure=exposure() if exposure_data is UNSET else exposure_data,
        side="BUY", quantity_lots=1, as_of=AS_OF,
    )


def test_authorized_fx_paper_sim_uses_ask_and_prices_half_spread_explicitly():
    result = simulate()
    assert result.status == "PAPER_SIMULATED"
    assert result.simulated_price == "83.12"
    assert result.estimated_spread_cost_inr == "10.00"
    assert result.evidence_sha256 == simulate().evidence_sha256
    assert len(rbi_rulebook_sha256()) == 64


def test_machine_readable_fx_rulebook_matches_runtime_policy():
    path = Path(__file__).parents[1] / "docs/edge-program/fx-paper-rulebook.v1.json"
    rulebook = json.loads(path.read_text(encoding="utf-8"))
    assert rulebook["rulebook_sha256"] == rbi_rulebook_sha256()
    assert rulebook["pairs"] == sorted(PAIRS)
    assert rulebook["products"] == sorted(PRODUCTS)
    assert rulebook["supported_venues"] == sorted(SUPPORTED_VENUES)


@pytest.mark.parametrize("jurisdiction", ["US", "ZZ"])
def test_unknown_jurisdiction_is_no_trade(jurisdiction):
    result = simulate(spec=contract(jurisdiction=jurisdiction))
    assert result.status == "NO_TRADE"
    assert "UNKNOWN_OR_UNSUPPORTED_JURISDICTION" in result.reasons


@pytest.mark.parametrize("venue_data,reason", [
    (None, "MISSING_VENUE_AUTHORIZATION"),
    (venue(authorized=False), "VENUE_NOT_AUTHORIZED"),
    (venue(venue_kind="UNVERIFIED_PLATFORM"), "UNSUPPORTED_VENUE_KIND"),
    (venue(effective_through=date(2026, 11, 13)), "VENUE_AUTHORIZATION_OUT_OF_DATE"),
    (venue(checked_at=AS_OF - timedelta(days=31)), "STALE_VENUE_AUTHORIZATION_EVIDENCE"),
    (venue(venue_id="UNKNOWN"), "VENUE_IDENTITY_MISMATCH"),
])
def test_unknown_or_unverified_venue_is_no_trade(venue_data, reason):
    result = simulate(venue_data=venue_data)
    assert result.status == "NO_TRADE"
    assert reason in result.reasons


def test_unknown_venue_stays_no_trade_even_if_caller_claims_authorization():
    result = simulate(spec=contract(venue_id="UNKNOWN"), venue_data=venue(venue_id="UNKNOWN"))
    assert result.status == "NO_TRADE"
    assert "UNKNOWN_OR_UNSUPPORTED_VENUE" in result.reasons


@pytest.mark.parametrize("spec,reason", [
    (contract(pair="AUD-USD"), "UNKNOWN_OR_UNSUPPORTED_INSTRUMENT"),
    (contract(product="SPOT"), "UNKNOWN_OR_UNSUPPORTED_INSTRUMENT"),
    (contract(settlement_currency="USD"), "INVALID_SETTLEMENT_CURRENCY"),
    (contract(product="CALL", underlying_kind="FUTURE", exercise_style="AMERICAN"),
     "INVALID_FX_OPTION_TERMS"),
    (contract(effective_through=date(2026, 11, 13)), "CONTRACT_SPEC_OUT_OF_DATE"),
    (contract(expiry=date(2027, 12, 1)), "INVALID_OR_OVER_12_MONTH_TENOR"),
])
def test_unknown_or_invalid_contract_spec_is_no_trade(spec, reason):
    result = simulate(spec=spec)
    assert result.status == "NO_TRADE"
    assert reason in result.reasons


@pytest.mark.parametrize("exposure_data,reason", [
    (None, "MISSING_CONTRACTED_FX_EXPOSURE"),
    (exposure(contracted=False), "INVALID_OR_STALE_CONTRACTED_FX_EXPOSURE"),
    (exposure(unhedged=False), "INVALID_OR_STALE_CONTRACTED_FX_EXPOSURE"),
    (exposure(pair="EUR-INR"), "INVALID_OR_STALE_CONTRACTED_FX_EXPOSURE"),
    (exposure(units=Decimal("999")), "INVALID_OR_STALE_CONTRACTED_FX_EXPOSURE"),
])
def test_inr_fx_requires_matching_unhedged_contracted_exposure(exposure_data, reason):
    result = simulate(exposure_data=exposure_data)
    assert result.status == "NO_TRADE"
    assert reason in result.reasons


def test_cash_settled_european_cross_pair_can_be_simulated_without_hedge_purpose():
    spec = contract(pair="EUR-USD", product="CALL", underlying_kind="SPOT",
                    exercise_style="EUROPEAN")
    result = simulate(spec=spec, exposure_data=None)
    assert result.status == "PAPER_SIMULATED"


@pytest.mark.parametrize("quote_data,reason", [
    (None, "MISSING_FX_QUOTE"),
    (quote(available_at=AS_OF - timedelta(seconds=61),
           observed_at=AS_OF - timedelta(seconds=62)), "STALE_FX_QUOTE"),
    (quote(available_at=AS_OF + timedelta(seconds=1),
           observed_at=AS_OF + timedelta(seconds=1)), "FUTURE_FX_QUOTE"),
])
def test_missing_stale_or_future_fx_quote_is_no_trade(quote_data, reason):
    result = simulate(market_quote=quote_data)
    assert result.status == "NO_TRADE"
    assert reason in result.reasons


def test_invalid_provenance_or_nonpositive_market_data_is_rejected():
    with pytest.raises(FxPaperError, match="SHA-256"):
        contract(source_sha256="bad")
    with pytest.raises(FxPaperError, match="bid/ask"):
        quote(bid=Decimal("0"))
