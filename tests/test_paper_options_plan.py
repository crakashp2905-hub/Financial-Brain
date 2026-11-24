from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.paper.options import OptionPaperError, Quote
from financial_brain.paper.options_plan import OptionContract, build_long_option_plan
from financial_brain.kernel.units import OptionLots


NOW = datetime(2026, 11, 22, 5, 0, tzinfo=timezone.utc)


def _contract(option_type="CE"):
    quote = Quote(123, f"NIFTY26NOV25000{option_type}", date(2026, 11, 26), 25,
                  NOW - timedelta(seconds=2), 100, 102, 500, 500)
    return OptionContract("NIFTY", option_type, 25_000, quote)


def test_call_plan_binds_exact_contract_risk_and_cost_hurdle():
    plan = build_long_option_plan(_contract(), decision_at=NOW,
                                  underlying_price=25_100, invalidation_price=25_000,
                                  lots=2, stop_premium=80, target_premium=140,
                                  max_quote_age=timedelta(seconds=5))
    assert (plan["option_type"], plan["side"], plan["token"], plan["strike"]) == ("CE", "BUY", 123, 25_000)
    assert plan["quantity"] == 50
    assert plan["entry_premium_band"] == {"bid": 100, "ask": 102}
    assert plan["underlying_invalidation"] == {"operator": "<=", "price": 25_000,
                                                "decision_price": 25_100}
    assert plan["projected_reward_inr"] > plan["projected_round_trip_charges_inr"]
    assert plan["broker_order_created"] is False


def test_call_plan_accepts_nominal_option_lots():
    plan = build_long_option_plan(_contract(), decision_at=NOW,
                                  underlying_price=25_100, invalidation_price=25_000,
                                  lots=OptionLots(2), stop_premium=80, target_premium=140,
                                  max_quote_age=timedelta(seconds=5))
    assert plan["lots"] == 2 and plan["quantity"] == 50


def test_put_plan_uses_opposite_underlying_invalidation():
    plan = build_long_option_plan(_contract("PE"), decision_at=NOW,
                                  underlying_price=24_900, invalidation_price=25_000,
                                  lots=1, stop_premium=80, target_premium=140,
                                  max_quote_age=timedelta(seconds=5))
    assert plan["underlying_invalidation"]["operator"] == ">="


@pytest.mark.parametrize("kwargs", [
    {"invalidation_price": 25_200},
    {"stop_premium": 102},
    {"target_premium": 102.01},
])
def test_plan_rejects_invalid_direction_levels_or_cost_hurdle(kwargs):
    args = {"invalidation_price": 25_000, "stop_premium": 80, "target_premium": 140}
    args.update(kwargs)
    with pytest.raises(OptionPaperError):
        build_long_option_plan(_contract(), decision_at=NOW,
                               underlying_price=25_100, lots=1,
                               max_quote_age=timedelta(seconds=5), **args)


def test_plan_rejects_stale_quote_and_insufficient_visible_size():
    c = _contract()
    with pytest.raises(OptionPaperError, match="stale"):
        build_long_option_plan(c, decision_at=NOW, underlying_price=25_100,
                               invalidation_price=25_000, lots=1, stop_premium=80,
                               target_premium=140, max_quote_age=timedelta(seconds=1))
    thin = OptionContract("NIFTY", "CE", 25_000,
                          Quote(123, "NIFTY26NOV25000CE", date(2026, 11, 26), 25,
                                NOW, 100, 102, 500, 24))
    with pytest.raises(OptionPaperError, match="depth"):
        build_long_option_plan(thin, decision_at=NOW, underlying_price=25_100,
                               invalidation_price=25_000, lots=1, stop_premium=80,
                               target_premium=140, max_quote_age=timedelta(seconds=1))


def test_plan_rejects_tradingsymbol_that_disagrees_with_declared_contract():
    c = OptionContract("NIFTY", "PE", 24_900, _contract("PE").quote)
    with pytest.raises(OptionPaperError, match="tradingsymbol"):
        build_long_option_plan(c, decision_at=NOW, underlying_price=25_100,
                               invalidation_price=25_000, lots=1, stop_premium=80,
                               target_premium=140, max_quote_age=timedelta(seconds=5))
