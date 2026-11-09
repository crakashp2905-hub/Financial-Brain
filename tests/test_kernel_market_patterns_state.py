from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from financial_brain.kernel.market_rules import TicketRules, session_gaps, validate_ticket
from financial_brain.kernel.patterns import (
    IllegalTransition,
    PatternLifecycle,
    PatternState,
    confirmed_pivots,
    replay,
)
from financial_brain.kernel.strategy_state import StrategyState, trigger_id

RULES = TicketRules(lot_size=25, min_notional=Decimal("1000"), max_freeze_quantity=1800)


def test_valid_ticket_has_no_reasons() -> None:
    assert validate_ticket(50, Decimal("100"), RULES) == []


def test_ticket_violations_are_all_reported() -> None:
    reasons = validate_ticket(30, Decimal("1"), RULES)
    assert any("lot size" in r for r in reasons)
    assert any("notional" in r for r in reasons)
    assert validate_ticket(-5, Decimal("100"), RULES)
    assert any("freeze" in r for r in validate_ticket(1825, Decimal("100"), RULES))


def test_holidays_are_not_reported_as_failures() -> None:
    expected = [date(2026, 11, 9), date(2026, 11, 10), date(2026, 11, 11)]
    observed = {date(2026, 11, 9)}
    holidays = {date(2026, 11, 10)}
    report = session_gaps(expected, observed, holidays)
    assert report.holiday_gaps == [date(2026, 11, 10)]
    assert report.unexplained_gaps == [date(2026, 11, 11)]


def test_pivot_is_not_confirmed_before_k_later_bars() -> None:
    highs = [1, 2, 9, 3, 2, 1, 1]
    lows = [1, 1, 1, 1, 1, 1, 1]
    assert [p.index for p in confirmed_pivots(highs, lows, 2, upto=3)] == []
    pivots = confirmed_pivots(highs, lows, 2, upto=4)
    assert [(p.index, p.confirmed_at, p.kind) for p in pivots] == [(2, 4, "HIGH")]


def test_truncating_data_never_alters_an_earlier_confirmed_pivot() -> None:
    highs = [1, 2, 9, 3, 2, 8, 1, 1]
    lows = [0, 0, 0, 0, 0, 0, 0, 0]
    full = confirmed_pivots(highs, lows, 2, upto=7)
    truncated = confirmed_pivots(highs[:6], lows[:6], 2, upto=5)
    for pivot in truncated:
        assert pivot in full


def test_lifecycle_terminal_states_refuse_further_moves() -> None:
    life = PatternLifecycle()
    life.move(PatternState.CONFIRMED)
    with pytest.raises(IllegalTransition):
        life.move(PatternState.FAILED)


def test_replay_is_deterministic() -> None:
    events = [PatternState.CONFIRMED]
    assert replay(events) == replay(list(events)) == PatternState.CONFIRMED
    with pytest.raises(IllegalTransition):
        replay([PatternState.EXPIRED, PatternState.CONFIRMED])


def test_duplicate_trigger_is_ignored_on_replay() -> None:
    state = StrategyState(cooldown_bars=3, max_concurrent=2)
    tid = trigger_id("orb", "NSE:ABC", 10)
    assert state.trigger(tid, "NSE:ABC", 10) is True
    assert state.trigger(tid, "NSE:ABC", 10) is False


def test_cooldown_and_max_concurrent_limits() -> None:
    state = StrategyState(cooldown_bars=5, max_concurrent=1)
    assert state.trigger("a", "X", 0) is True
    assert state.trigger("b", "X", 2) is False  # cooldown
    assert state.trigger("c", "Y", 3) is False  # max concurrent reached
    state.close("X")
    assert state.trigger("d", "Y", 3) is True
