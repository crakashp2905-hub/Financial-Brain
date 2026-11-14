from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.paper.options import Quote
from financial_brain.paper.options_spread import Leg, OptionLegEvidence
from financial_brain.paper.options_spread_lifecycle import (
    OptionFill,
    SpreadLifecycleError,
    SpreadLifecyclePlan,
    evaluate_spread_lifecycle,
)


ASOF = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)
EXPIRY = date(2026, 11, 26)
HASH_A, HASH_B, HASH_C = "a" * 64, "b" * 64, "c" * 64


def _plan(**overrides):
    values = dict(
        underlying="NIFTY", venue="NFO", option_type="CE", expiry=EXPIRY,
        lot_size=10, target_lots=2, long_token=1, long_strike=Decimal("100"),
        short_token=2, short_strike=Decimal("110"),
        defined_spread_margin_per_lot=Decimal("500"),
        naked_short_margin_per_lot=Decimal("5000"),
        long_contract_sha256=HASH_A, short_contract_sha256=HASH_B,
        margin_source_sha256=HASH_C,
    )
    values.update(overrides)
    return SpreadLifecyclePlan(**values)


def _fill(event_id, token, action, lots, price, fee="0.00", *, at=None):
    return OptionFill(event_id, token, action, lots, Decimal(price), Decimal(fee),
                      at or ASOF - timedelta(seconds=2), HASH_A)


def _close(token, strike, bid, ask, *, bid_qty=20, ask_qty=20, available=None,
           contract_sha=None, option_type="CE"):
    observed = ASOF - timedelta(seconds=1)
    quote = Quote(token, f"NIFTY-{strike}-{option_type}", EXPIRY, 10,
                  observed, bid, ask, bid_qty, ask_qty)
    leg = Leg(quote, "NIFTY", option_type, strike)
    return OptionLegEvidence(
        leg, "NFO", "NFO", available or ASOF - timedelta(milliseconds=100),
        HASH_B, contract_sha or (HASH_A if token == 1 else HASH_B), EXPIRY, 10,
    )


def test_partial_spread_retains_pending_lots_margin_and_tail_scenarios():
    fills = [_fill("buy-1", 1, "BUY", 1, "12", "2"),
             _fill("sell-1", 2, "SELL", 1, "5", "1")]
    report = evaluate_spread_lifecycle(
        _plan(), fills, as_of=ASOF,
        close_quotes={1: _close(1, 100, 14, 15), 2: _close(2, 110, 6, 7)},
        stress_spot_prices=[Decimal("90"), Decimal("120")],
    )
    assert report["status"] == "PARTIAL_OR_LEGGED"
    assert report["unfilled_lots"] == {"1": 1, "2": 1}
    assert report["unmatched_long_lots"] == report["unmatched_short_lots"] == 0
    assert report["margin_reserved_inr"] == "500"
    assert report["closeout_status"] == "FULL"
    assert report["closeout_net_pnl_inr"] is not None
    assert report["worst_stress_pnl_inr"] == "-73"


def test_short_first_fill_retains_naked_margin_and_closeout_risk():
    report = evaluate_spread_lifecycle(
        _plan(), [_fill("sell-first", 2, "SELL", 1, "5", "1")],
        as_of=ASOF, close_quotes={},
        stress_spot_prices=[Decimal("90"), Decimal("140")],
    )
    assert report["legging_exposure"] is True
    assert report["unmatched_short_lots"] == 1
    assert report["margin_reserved_inr"] == "5000"
    assert report["closeout_status"] == "PARTIAL_OR_UNRESOLVED"
    assert report["closeout_net_pnl_inr"] is None
    assert report["closeout_legs"][0]["reason"] == "MISSING_CLOSE_QUOTE"


def test_no_fill_case_remains_in_performance_with_all_lots_unfilled():
    report = evaluate_spread_lifecycle(
        _plan(), [], as_of=ASOF, close_quotes={},
        stress_spot_prices=[Decimal("100")],
    )
    assert report["status"] == "NO_FILLS"
    assert report["unfilled_lots"] == {"1": 2, "2": 2}
    assert report["worst_stress_pnl_inr"] == "0"


def test_exit_depth_partial_or_stale_never_marks_unclosed_leg_flat():
    fills = [_fill("buy", 1, "BUY", 2, "12"), _fill("sell", 2, "SELL", 2, "5")]
    report = evaluate_spread_lifecycle(
        _plan(), fills, as_of=ASOF,
        close_quotes={1: _close(1, 100, 14, 15, bid_qty=10),
                      2: _close(2, 110, 6, 7, available=ASOF + timedelta(seconds=1))},
        stress_spot_prices=[Decimal("90"), Decimal("120")],
    )
    assert report["closeout_status"] == "PARTIAL_OR_UNRESOLVED"
    assert report["closeout_net_pnl_inr"] is None
    assert report["closeout_legs"][0]["closed_lots"] == 1
    assert report["closeout_legs"][1]["closed_lots"] == 0


def test_future_or_duplicate_fill_and_overfill_are_rejected():
    future = _fill("late", 1, "BUY", 1, "12", at=ASOF + timedelta(seconds=1))
    with pytest.raises(SpreadLifecycleError, match="available by as_of"):
        evaluate_spread_lifecycle(_plan(), [future], as_of=ASOF, close_quotes={},
                                  stress_spot_prices=[Decimal("100")])
    duplicate = _fill("same", 1, "BUY", 1, "12")
    with pytest.raises(SpreadLifecycleError, match="unique"):
        evaluate_spread_lifecycle(_plan(), [duplicate, duplicate], as_of=ASOF,
                                  close_quotes={}, stress_spot_prices=[Decimal("100")])
    over = [_fill("a", 1, "BUY", 2, "12"), _fill("b", 1, "BUY", 1, "12")]
    with pytest.raises(SpreadLifecycleError, match="exceed"):
        evaluate_spread_lifecycle(_plan(), over, as_of=ASOF, close_quotes={},
                                  stress_spot_prices=[Decimal("100")])


def test_close_quote_must_match_contract_master_and_lot_terms():
    fills = [_fill("buy", 1, "BUY", 1, "12")]
    stale_terms = _close(1, 100, 14, 15, contract_sha=HASH_C)
    report = evaluate_spread_lifecycle(
        _plan(), fills, as_of=ASOF, close_quotes={1: stale_terms},
        stress_spot_prices=[Decimal("90")],
    )
    assert report["closeout_legs"][0]["reason"] == "CONTRACT_TERMS_MISMATCH"
    assert report["closeout_net_pnl_inr"] is None
