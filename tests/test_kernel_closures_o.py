from __future__ import annotations

from decimal import Decimal

import pytest

from financial_brain.kernel.closures_o import MarkSnapshot, seeded_random_policy


def test_stochastic_policy_is_reproducible_from_its_seed() -> None:
    actions = ["BUY", "HOLD", "SELL"]
    a = seeded_random_policy(seed=5, actions=actions, steps=20)
    assert a == seeded_random_policy(seed=5, actions=actions, steps=20)
    assert a != seeded_random_policy(seed=6, actions=actions, steps=20)
    assert set(a) <= set(actions)
    with pytest.raises(ValueError):
        seeded_random_policy(seed=1, actions=[], steps=3)


def test_mark_uses_mid_when_quoted_and_last_otherwise() -> None:
    quoted = MarkSnapshot(bid=Decimal("99"), ask=Decimal("101"), last=Decimal("150"))
    assert quoted.mark() == Decimal("100")
    unquoted = MarkSnapshot(bid=None, ask=None, last=Decimal("150"))
    assert unquoted.mark() == Decimal("150")
    assert MarkSnapshot(bid=None, ask=None, last=None).mark() is None


def test_crossed_quote_is_not_marked() -> None:
    with pytest.raises(ValueError, match="crossed"):
        MarkSnapshot(bid=Decimal("101"), ask=Decimal("99"), last=None).mark()
