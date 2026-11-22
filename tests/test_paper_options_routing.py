from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from financial_brain.costs.india import CostModel
from financial_brain.paper.options import Quote
from financial_brain.paper.options_routing import (
    OptionContractVersion,
    OptionQuoteObservation,
    OptionRoutingError,
    OptionRoutingPolicy,
    UnderlyingPriceObservation,
    route_long_option,
)

UTC = timezone.utc
DECISION = datetime(2026, 11, 2, 10, tzinfo=UTC)
HASH_A = "a" * 64
HASH_B = "b" * 64


def contract(*, strike="25000", expiry=date(2026, 11, 16), token=1, kind="CE",
             published_at=DECISION - timedelta(days=2), effective_from=date(2026, 1, 1),
             effective_to=None, lot_size=25, symbol=None, source_hash=HASH_A):
    symbol = symbol or f"NIFTY{expiry:%d%b%y}{strike}{kind}"
    return OptionContractVersion("NIFTY", kind, D(strike), expiry, symbol, token, lot_size,
                                 effective_from, effective_to, published_at, source_hash)


def observation(c, *, bid="99", ask="100", bid_qty=100, ask_qty=100,
                observed_at=DECISION - timedelta(milliseconds=500),
                available_at=DECISION - timedelta(milliseconds=250), source="KITE"):
    quote = Quote(c.token, c.symbol, c.expiry, c.lot_size, observed_at,
                  float(bid), float(ask), bid_qty, ask_qty)
    return OptionQuoteObservation(quote, available_at, source, "NFO", HASH_B)


def spot(*, observed_at=DECISION - timedelta(milliseconds=500),
         available_at=DECISION - timedelta(milliseconds=300)):
    return UnderlyingPriceObservation("NIFTY", D("25000"), observed_at,
                                      available_at, "KITE", "NSE", HASH_A)


def policy(**kwargs):
    return OptionRoutingPolicy(include_impact_cost=False, **kwargs)


def test_router_uses_only_published_effective_contracts_and_quotes_available_at_decision():
    atm = contract(token=1)
    better_moneyness_but_late_contract = contract(token=2, published_at=DECISION + timedelta(seconds=1))
    future_quote = observation(atm, available_at=DECISION + timedelta(milliseconds=1))
    result = route_long_option(
        underlying="NIFTY", direction="CE", decision_at=DECISION,
        underlying_observation=spot(), contracts=[atm, better_moneyness_but_late_contract],
        observations=[observation(atm), future_quote, observation(better_moneyness_but_late_contract)],
        policy=policy(), costs=CostModel(),
    )
    assert result["status"] == "PAPER_PLAN"
    assert result["token"] == 1
    assert result["simulation_fidelity"] == "OBSERVED_QUOTE_SIM"
    assert result["quote_source"] == "KITE" and result["quote_exchange"] == "NFO"
    assert result["contract_version_sha256"]
    assert result["quote_provenance_sha256"]


def test_router_selects_target_expiry_and_atm_strike_deterministically():
    atm_target_expiry = contract(strike="25000", expiry=date(2026, 11, 16), token=5)
    otm_target_expiry = contract(strike="25100", expiry=date(2026, 11, 16), token=4)
    atm_other_expiry = contract(strike="25000", expiry=date(2026, 11, 23), token=2)
    entries = [atm_other_expiry, otm_target_expiry, atm_target_expiry]
    result = route_long_option(
        underlying="NIFTY", direction="CE", decision_at=DECISION,
        underlying_observation=spot(), contracts=entries,
        observations=[observation(c) for c in entries],
        policy=policy(target_dte_days=14), costs=CostModel(),
    )
    assert result["token"] == 5
    assert result["dte_days"] == 14


def test_router_sizes_in_whole_lots_within_all_in_premium_budget_and_visible_depth():
    c = contract()
    result = route_long_option(
        underlying="NIFTY", direction="CE", decision_at=DECISION,
        underlying_observation=spot(), contracts=[c], observations=[observation(c, ask_qty=75)],
        policy=policy(premium_budget_inr=D("5100"), max_lots=10), costs=CostModel(),
    )
    assert result["status"] == "PAPER_PLAN"
    assert result["lots"] == 2
    assert result["quantity"] == 50
    assert D(result["all_in_premium_risk_inr"]) <= D("5100")
    assert result["broker_order_created"] is False
    assert result["underlying_spot_provenance_sha256"]
    assert result["cost_model_sha256"]


def test_router_supports_long_puts_without_short_premium_fallback():
    put = contract(strike="25000", kind="PE", token=9)
    result = route_long_option(
        underlying="NIFTY", direction="PE", decision_at=DECISION,
        underlying_observation=spot(), contracts=[put], observations=[observation(put)],
        policy=policy(), costs=CostModel(),
    )
    assert result["status"] == "PAPER_PLAN"
    assert result["side"] == "BUY" and result["option_type"] == "PE"
    assert result["all_in_premium_risk_inr"]


def test_router_returns_no_trade_when_one_lot_exceeds_budget_or_depth_is_missing():
    c = contract()
    too_expensive = route_long_option(
        underlying="NIFTY", direction="CE", decision_at=DECISION,
        underlying_observation=spot(), contracts=[c], observations=[observation(c)],
        policy=policy(premium_budget_inr=D("2499")), costs=CostModel(),
    )
    no_depth = route_long_option(
        underlying="NIFTY", direction="CE", decision_at=DECISION,
        underlying_observation=spot(), contracts=[c],
        observations=[observation(c, ask_qty=24, bid_qty=24)],
        policy=policy(), costs=CostModel(),
    )
    assert too_expensive["status"] == no_depth["status"] == "NO_TRADE"
    assert "ONE_LOT_EXCEEDS_PREMIUM_BUDGET" in too_expensive["rejections"].values()
    assert "VISIBLE_DEPTH_BELOW_ONE_LOT" in no_depth["rejections"].values()


def test_router_rejects_stale_crossed_mismatched_and_expired_contract_evidence():
    live = contract()
    expired = contract(token=2, expiry=date(2026, 10, 31))
    wrong_quote_identity = OptionQuoteObservation(
        Quote(live.token, "OTHER", live.expiry, live.lot_size, DECISION - timedelta(seconds=1),
              99.0, 100.0, 100, 100), DECISION - timedelta(milliseconds=250), "KITE", "NFO", HASH_B)
    stale = observation(live, observed_at=DECISION - timedelta(minutes=2),
                         available_at=DECISION - timedelta(minutes=1))
    crossed = observation(live, bid="101", ask="100")
    result = route_long_option(
        underlying="NIFTY", direction="CE", decision_at=DECISION,
        underlying_observation=spot(), contracts=[live, expired],
        observations=[wrong_quote_identity, stale, crossed], policy=policy(), costs=CostModel(),
    )
    assert result["status"] == "NO_TRADE"
    assert result["rejections"]["2"] == "CONTRACT_NOT_EFFECTIVE_OR_NOT_YET_PUBLISHED"


def test_contract_and_quote_provenance_change_their_hashes():
    c = contract()
    q = observation(c)
    assert c.version_sha256() != contract(source_hash=HASH_B).version_sha256()
    assert q.provenance_sha256() != observation(c, source="OTHER_FEED").provenance_sha256()


def test_contract_identity_and_policy_reject_malformed_inputs():
    with pytest.raises(OptionRoutingError, match="symbol does not match"):
        contract(symbol="NIFTY16NOV2624900CE")
    with pytest.raises(OptionRoutingError, match="budget"):
        policy(premium_budget_inr=1000)


def test_router_refuses_stale_underlying_spot_for_moneyness_selection():
    c = contract()
    with pytest.raises(OptionRoutingError, match="stale"):
        route_long_option(
            underlying="NIFTY", direction="CE", decision_at=DECISION,
            underlying_observation=spot(observed_at=DECISION - timedelta(minutes=2),
                                       available_at=DECISION - timedelta(minutes=1)),
            contracts=[c], observations=[observation(c)], policy=policy(), costs=CostModel(),
        )
