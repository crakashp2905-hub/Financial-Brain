from datetime import date, datetime, timedelta, timezone

from financial_brain.paper.options import Quote
from financial_brain.paper.options_exit import (
    OptionExitDirective,
    OptionExitQuote,
    PremiumExitPolicy,
    manage_long_option_exit,
)


ENTRY_AT = datetime(2026, 11, 27, 5, tzinfo=timezone.utc)
EXPIRY = date(2026, 11, 30)


def _quote(at, bid, bid_qty, *, ask=None, ask_qty=100):
    return Quote(321, "NIFTY26NOV25000CE", EXPIRY, 10, at, bid,
                 ask if ask is not None else bid + 0.1, bid_qty, ask_qty)


def _policy():
    return PremiumExitPolicy(
        stop_premium=8, target_premium=12,
        expiry_exit_at=datetime(2026, 11, 29, 9, tzinfo=timezone.utc),
        contract_expiry_at=datetime(2026, 11, 30, 10, tzinfo=timezone.utc),
    )


def test_stop_triggers_on_observed_bid_and_partial_fills_remain_queued():
    entry = _quote(ENTRY_AT, 9.9, 100, ask=10, ask_qty=100)
    times = (ENTRY_AT + timedelta(seconds=1), ENTRY_AT + timedelta(seconds=2))
    result = manage_long_option_exit(
        entry=entry, lots=2, policy=_policy(), max_quote_age_seconds=2,
        quotes=(OptionExitQuote(_quote(times[0], 7.9, 7), times[0] + timedelta(milliseconds=10)),
                OptionExitQuote(_quote(times[1], 8.1, 13), times[1] + timedelta(milliseconds=10))),
    )
    assert result["status"] == "CLOSED"
    assert result["trigger"] == "PREMIUM_STOP"
    assert result["filled_contracts"] == 20 and result["remaining_contracts"] == 0
    assert [row["quantity"] for row in result["fills"]] == [7, 13]
    assert all(row["reason"] == "PREMIUM_STOP" for row in result["fills"])
    assert result["broker_order_created"] is False


def test_target_uses_observed_bid_and_stale_or_expired_quotes_do_not_fill():
    entry = _quote(ENTRY_AT, 9.9, 100, ask=10, ask_qty=100)
    target_at = ENTRY_AT + timedelta(seconds=1)
    target = manage_long_option_exit(
        entry=entry, lots=1, policy=_policy(), max_quote_age_seconds=2,
        quotes=(OptionExitQuote(_quote(target_at, 12.1, 10), target_at + timedelta(milliseconds=5)),),
    )
    assert target["status"] == "CLOSED" and target["trigger"] == "PREMIUM_TARGET"
    stale_at = ENTRY_AT + timedelta(seconds=1)
    stale = manage_long_option_exit(
        entry=entry, lots=1, policy=_policy(), max_quote_age_seconds=1,
        quotes=(OptionExitQuote(_quote(stale_at, 7, 10), stale_at + timedelta(seconds=2)),),
    )
    assert stale["status"] == "OPEN" and stale["filled_contracts"] == 0


def test_expiry_cutoff_requires_a_pre_expiry_executable_quote():
    entry = _quote(ENTRY_AT, 9.9, 100, ask=10, ask_qty=100)
    cutoff_quote_at = _policy().expiry_exit_at + timedelta(seconds=1)
    result = manage_long_option_exit(
        entry=entry, lots=1, policy=_policy(), max_quote_age_seconds=1,
        quotes=(OptionExitQuote(_quote(cutoff_quote_at, 9, 10),
                                cutoff_quote_at + timedelta(milliseconds=10)),),
    )
    assert result["status"] == "CLOSED" and result["trigger"] == "EXPIRY_CUTOFF"
    expired_at = _policy().contract_expiry_at
    unresolved = manage_long_option_exit(
        entry=entry, lots=1, policy=_policy(), max_quote_age_seconds=1,
        quotes=(OptionExitQuote(_quote(expired_at, 20, 10), expired_at),),
    )
    assert unresolved["status"] == "OPEN"
    assert unresolved["unresolved_at_expiry"] is True


def test_preregistered_reversal_and_time_rules_trigger_at_observed_quotes():
    entry = _quote(ENTRY_AT, 9.9, 100, ask=10, ask_qty=100)
    available = ENTRY_AT + timedelta(seconds=1)
    result = manage_long_option_exit(
        entry=entry, lots=1, policy=_policy(), max_quote_age_seconds=1,
        directives=(OptionExitDirective("reverse-1", "SIGNAL_REVERSAL", available,
                                        "a" * 64),),
        quotes=(OptionExitQuote(_quote(available, 9, 10), available),),
    )
    assert result["trigger"] == "SIGNAL_REVERSAL"
    assert result["fills"][0]["reason"] == "SIGNAL_REVERSAL"
    assert len(result["exit_policy_sha256"]) == 64
    assert len(result["cost_assumptions_sha256"]) == 64
    restricted = PremiumExitPolicy(_policy().stop_premium, _policy().target_premium,
                                   _policy().expiry_exit_at, _policy().contract_expiry_at,
                                   registered_event_triggers=("TIME_STOP",))
    import pytest
    from financial_brain.paper.options import OptionPaperError
    with pytest.raises(OptionPaperError, match="pre-registered"):
        manage_long_option_exit(
            entry=entry, lots=1, policy=restricted, max_quote_age_seconds=1,
            directives=(OptionExitDirective("vol-1", "VOLATILITY_SHIFT", available,
                                            "b" * 64),), quotes=(),
        )


def test_exit_quote_observed_before_trigger_cannot_be_used_after_late_receipt():
    entry = _quote(ENTRY_AT, 9.9, 100, ask=10, ask_qty=100)
    trigger_at = ENTRY_AT + timedelta(seconds=5)
    old_quote_at = trigger_at - timedelta(seconds=1)
    old_quote = OptionExitQuote(_quote(old_quote_at, 9, 10), trigger_at + timedelta(milliseconds=1))
    directive = OptionExitDirective("reversal", "SIGNAL_REVERSAL", trigger_at, "c" * 64)
    blocked = manage_long_option_exit(entry=entry, lots=1, policy=_policy(), directives=(directive,),
                                      quotes=(old_quote,), max_quote_age_seconds=10)
    assert blocked["trigger"] == "SIGNAL_REVERSAL"
    assert blocked["status"] == "OPEN" and blocked["filled_contracts"] == 0

    fresh_at = trigger_at + timedelta(seconds=1)
    fresh = OptionExitQuote(_quote(fresh_at, 9, 10), fresh_at + timedelta(milliseconds=5))
    closed = manage_long_option_exit(entry=entry, lots=1, policy=_policy(), directives=(directive,),
                                     quotes=(old_quote, fresh), max_quote_age_seconds=10)
    assert closed["status"] == "CLOSED" and closed["filled_contracts"] == 10
    assert closed["fills"][0]["observed_at"] == fresh_at.isoformat()


def test_stop_trigger_without_exchange_bid_depth_never_invents_exit_liquidity():
    entry = _quote(ENTRY_AT, 9.9, 100, ask=10, ask_qty=100)
    stop_at = ENTRY_AT + timedelta(seconds=1)
    no_depth = OptionExitQuote(_quote(stop_at, 7, 0), stop_at)
    result = manage_long_option_exit(entry=entry, lots=1, policy=_policy(),
                                     quotes=(no_depth,), max_quote_age_seconds=2)
    assert result["trigger"] == "PREMIUM_STOP"
    assert result["status"] == "OPEN" and result["filled_contracts"] == 0
    assert result["remaining_contracts"] == 10
