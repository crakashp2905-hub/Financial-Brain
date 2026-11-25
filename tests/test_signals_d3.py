"""D3 signal contract: one schema for five desks, contradictory plans rejected before any ledger."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from financial_brain.signals.contracts import (
    Desk,
    EvidenceTier,
    ExitPolicy,
    ExitTrigger,
    Intent,
    Lifecycle,
    Side,
    Signal,
    SignalError,
    advance,
    on_tick,
)

T0 = datetime(2026, 11, 17, 9, 30, tzinfo=timezone.utc)
T1 = datetime(2026, 11, 17, 9, 45, tzinfo=timezone.utc)
T2 = datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc)
POLICY = ExitPolicy((ExitTrigger.HARD_STOP, ExitTrigger.TAKE_PROFIT, ExitTrigger.TIME_STOP), "v1")
INTRADAY_POLICY = ExitPolicy((ExitTrigger.HARD_STOP, ExitTrigger.TAKE_PROFIT,
                              ExitTrigger.SESSION_FLATTEN), "v1")


def sig(**over) -> Signal:
    base = dict(
        signal_id="S-1", desk=Desk.SWING, strategy_id="swing5", strategy_version="v1",
        strategy_hash="abc", instrument_id="IN:INE000", intent=Intent.EXCHANGE_LIMIT,
        as_of=T0, decision_at=T1, expires_at=T2, evidence_tier=EvidenceTier.LOCAL_PAPER,
        source_hash="src", side=Side.BUY, quantity=Decimal("10"), entry_limit=Decimal("100.00"),
        stop_price=Decimal("97.00"), target_price=Decimal("106.00"), tick_size=Decimal("0.05"),
        exit_policy=POLICY,
    )
    base.update(over)
    return Signal(**base)


# ---------- a valid plan


def test_valid_long_swing_plan_is_accepted():
    s = sig()
    assert s.side is Side.BUY
    assert s.gross_reward_risk() == Decimal("2")


def test_signal_is_immutable():
    with pytest.raises(FrozenInstanceError):
        sig().stop_price = Decimal("1")  # type: ignore[misc]


# ---------- timing


def test_decision_cannot_precede_as_of():
    with pytest.raises(SignalError):
        sig(decision_at=T0.replace(hour=8))


def test_expiry_must_follow_decision():
    with pytest.raises(SignalError):
        sig(expires_at=T1)


def test_naive_timestamp_is_refused():
    with pytest.raises(SignalError):
        sig(as_of=datetime(2026, 11, 17, 9, 30))


def test_required_text_fields_are_refused_when_empty():
    with pytest.raises(SignalError):
        sig(strategy_hash="")


# ---------- protection before entry (FB-0452, FB-0472)


@pytest.mark.parametrize("missing", ["stop_price", "target_price", "exit_policy", "entry_limit"])
def test_exchange_trade_missing_protection_or_entry_is_refused(missing):
    with pytest.raises(SignalError):
        sig(**{missing: None})


def test_exchange_trade_without_side_is_refused():
    with pytest.raises(SignalError):
        sig(side=None)


# ---------- price ordering (FB-0459), table-driven


@pytest.mark.parametrize("stop, target, ok", [
    ("97.00", "106.00", True),
    ("100.00", "106.00", False),   # stop equal to entry
    ("101.00", "106.00", False),   # stop above entry
    ("97.00", "99.00", False),     # target below entry on a long
])
def test_long_price_ordering(stop, target, ok):
    if ok:
        sig(stop_price=Decimal(stop), target_price=Decimal(target))
    else:
        with pytest.raises(SignalError):
            sig(stop_price=Decimal(stop), target_price=Decimal(target))


def test_short_plan_needs_target_below_entry_and_stop_above():
    s = sig(side=Side.SELL_SHORT, desk=Desk.INTRADAY, exit_policy=INTRADAY_POLICY,
            stop_price=Decimal("103.00"), target_price=Decimal("94.00"))
    assert s.gross_reward_risk() == Decimal("2")


def test_short_with_inverted_prices_is_refused():
    with pytest.raises(SignalError):
        sig(side=Side.SELL_SHORT, desk=Desk.INTRADAY, exit_policy=INTRADAY_POLICY,
            stop_price=Decimal("97.00"), target_price=Decimal("106.00"))


def test_overnight_equity_short_is_refused():
    with pytest.raises(SignalError):
        sig(side=Side.SELL_SHORT, stop_price=Decimal("103.00"), target_price=Decimal("94.00"))


# ---------- tick rounding (FB-0461)


def test_price_off_tick_is_refused():
    with pytest.raises(SignalError):
        sig(entry_limit=Decimal("100.03"))


def test_tick_check_helper():
    assert on_tick(Decimal("100.05"), Decimal("0.05"))
    assert not on_tick(Decimal("100.02"), Decimal("0.05"))
    with pytest.raises(SignalError):
        on_tick(Decimal("1"), Decimal("0"))


def test_exchange_trade_without_tick_size_is_refused():
    with pytest.raises(SignalError):
        sig(tick_size=None)


# ---------- desk and intent (FB-0403, FB-0102)


def test_intraday_exit_must_flatten_at_session_end():
    with pytest.raises(SignalError):
        sig(desk=Desk.INTRADAY, exit_policy=POLICY)


def test_intraday_with_session_flatten_is_accepted():
    sig(desk=Desk.INTRADAY, exit_policy=INTRADAY_POLICY)


def test_mutual_fund_desk_cannot_use_exchange_intent():
    with pytest.raises(SignalError):
        sig(desk=Desk.MUTUAL_FUND)


def test_exchange_desk_cannot_use_mf_intent():
    with pytest.raises(SignalError):
        sig(intent=Intent.MF_REDEEM, side=None, stop_price=None, target_price=None,
            entry_limit=None, exit_policy=None, quantity=Decimal("5"))


# ---------- NO_TRADE (FB-0462)


def test_no_trade_needs_a_reason_and_no_size():
    with pytest.raises(SignalError):
        sig(intent=Intent.NO_TRADE, reason=None, side=None, quantity=None, stop_price=None,
            target_price=None, entry_limit=None, exit_policy=None)


def test_no_trade_with_reason_is_accepted():
    s = sig(intent=Intent.NO_TRADE, reason="spread wider than plan", side=None, quantity=None,
            stop_price=None, target_price=None, entry_limit=None, exit_policy=None)
    assert s.reason == "spread wider than plan"


def test_no_trade_cannot_smuggle_a_side():
    with pytest.raises(SignalError):
        sig(intent=Intent.NO_TRADE, reason="x", quantity=None, stop_price=None,
            target_price=None, entry_limit=None, exit_policy=None)


# ---------- mutual funds: NAV thresholds, not stops (FB-0481)


def test_mf_signal_with_nav_action_is_accepted():
    s = sig(desk=Desk.MUTUAL_FUND, intent=Intent.MF_REDEEM, side=None, stop_price=None,
            target_price=None, entry_limit=None, tick_size=None, exit_policy=None,
            quantity=Decimal("12.345"))
    assert s.intent is Intent.MF_REDEEM


def test_mf_signal_cannot_carry_an_exchange_stop():
    with pytest.raises(SignalError):
        sig(desk=Desk.MUTUAL_FUND, intent=Intent.MF_SUBSCRIBE, side=None, entry_limit=None,
            tick_size=None, exit_policy=None, quantity=Decimal("1"))


# ---------- exit policy (FB-0454, FB-0459)


def test_exit_policy_needs_triggers_and_version():
    with pytest.raises(SignalError):
        ExitPolicy((), "v1")
    with pytest.raises(SignalError):
        ExitPolicy((ExitTrigger.HARD_STOP,), "")


def test_exit_policy_triggers_cannot_repeat():
    with pytest.raises(SignalError):
        ExitPolicy((ExitTrigger.HARD_STOP, ExitTrigger.HARD_STOP), "v1")


def test_exit_policy_without_hard_stop_needs_versioned_exception():
    with pytest.raises(SignalError):
        ExitPolicy((ExitTrigger.TIME_STOP,), "v1")
    ExitPolicy((ExitTrigger.TIME_STOP,), "v1", exception="approved-2026-11-17")


# ---------- economics (FB-0457, FB-0458)


def test_planned_loss_and_gross_reward():
    s = sig()
    assert s.planned_loss() == Decimal("30.00")   # (100-97) * 10


def test_net_reward_risk_deducts_cost_from_reward_and_adds_it_to_risk():
    s = sig()
    # reward 6 - 0.5 = 5.5 ; risk 3 + 0.5 = 3.5
    assert s.net_reward_risk(Decimal("0.5")) == Decimal("5.5") / Decimal("3.5")


def test_reward_risk_gate():
    s = sig(min_reward_risk=Decimal("2"))
    assert s.passes_reward_risk_gate(Decimal("0"))
    assert not s.passes_reward_risk_gate(Decimal("2"))  # (6-2)/(3+2) = 0.8


def test_negative_cost_is_refused():
    with pytest.raises(SignalError):
        sig().net_reward_risk(Decimal("-1"))


# ---------- lifecycle (FB-0405)


def test_lifecycle_happy_path():
    s = Lifecycle.DETECTED
    for nxt in (Lifecycle.ELIGIBLE, Lifecycle.APPROVED, Lifecycle.SUBMITTED,
                Lifecycle.FILLED, Lifecycle.EXITED):
        s = advance(s, nxt)
    assert s is Lifecycle.EXITED


def test_lifecycle_cannot_skip_to_fill_from_detected():
    with pytest.raises(SignalError):
        advance(Lifecycle.DETECTED, Lifecycle.FILLED)


def test_terminal_states_do_not_move():
    with pytest.raises(SignalError):
        advance(Lifecycle.REJECTED, Lifecycle.ELIGIBLE)
