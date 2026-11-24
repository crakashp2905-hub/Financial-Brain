from datetime import date
from decimal import Decimal

from financial_brain.paper.mf_redemption_constraints import resolve_redemption_eligibility
from financial_brain.providers.mf_scheme_terms import ExitRestriction


def test_unknown_restrictions_and_expired_elss_lock_in_fail_closed():
    unknown = resolve_redemption_eligibility(
        as_of=date(2026, 1, 1), requested_units=2, lot_units=5, restrictions=None,
    )
    locked = resolve_redemption_eligibility(
        as_of=date(2026, 1, 1), requested_units=2, lot_units=5,
        restrictions=(ExitRestriction.ELSS_LOCK_IN,), lock_in_until=date(2028, 1, 1),
    )
    assert not unknown.eligible and "RESTRICTIONS_UNKNOWN" in unknown.reasons
    assert not locked.eligible and "LOT_LOCKED" in locked.reasons


def test_closed_window_and_closed_redemption_are_rejected():
    closed_window = resolve_redemption_eligibility(
        as_of=date(2026, 1, 1), requested_units=1, lot_units=5,
        restrictions=(ExitRestriction.CLOSED_ENDED_WINDOW,), redemption_window_open=False,
    )
    closed_scheme = resolve_redemption_eligibility(
        as_of=date(2026, 1, 1), requested_units=1, lot_units=5,
        restrictions=(ExitRestriction.CLOSED_TO_REDEMPTION,),
    )
    assert not closed_window.eligible
    assert not closed_scheme.eligible


def test_gate_and_segregated_bucket_cap_redemption_units():
    gate = resolve_redemption_eligibility(
        as_of=date(2026, 1, 1), requested_units=5, lot_units=10,
        restrictions=(ExitRestriction.GATING,), gating_remaining_units=3,
    )
    segregated = resolve_redemption_eligibility(
        as_of=date(2026, 1, 1), requested_units=5, lot_units=10,
        restrictions=(ExitRestriction.SEGREGATED_PORTFOLIO,), segregated_portfolio_units=2,
    )
    assert not gate.eligible and gate.eligible_units == Decimal(0)
    assert "GATING_LIMIT" in gate.reasons
    assert not segregated.eligible and "SEGREGATED_PORTFOLIO_LIMIT" in segregated.reasons


def test_confirmed_unrestricted_and_within_gate_are_eligible():
    unrestricted = resolve_redemption_eligibility(
        as_of=date(2026, 1, 1), requested_units=2, lot_units=5, restrictions=(),
    )
    gated = resolve_redemption_eligibility(
        as_of=date(2026, 1, 1), requested_units=2, lot_units=5,
        restrictions=(ExitRestriction.GATING,), gating_remaining_units=3,
    )
    assert unrestricted.eligible and unrestricted.eligible_units == Decimal(2)
    assert gated.eligible and gated.eligible_units == Decimal(2)
