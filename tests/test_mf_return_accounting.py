from datetime import date
from decimal import Decimal

import pytest

from financial_brain.research.mf_return_accounting import (
    MfReturnError, forward_net_projection, observed_nav_change,
)


def test_observed_nav_ratio_is_not_charged_ter_again():
    observed = observed_nav_change(
        Decimal("100"), Decimal("110"),
        start_date=date(2025, 1, 1), end_date=date(2026, 1, 1),
    )
    assert observed.nav_change_percent == Decimal("10.0")
    assert observed.basis == "OBSERVED_NAV_RATIO;TER_ALREADY_REFLECTED"
    assert "ter" not in observed.__dataclass_fields__


def test_forward_projection_applies_ter_only_to_future_gross_assumption():
    projection = forward_net_projection(12, Decimal("1.00"), years=2)
    assert projection.projected_net_return_percent == Decimal("22.94374400")
    assert projection.basis == "FORWARD_ONLY;TER_APPLIED_SEPARATELY"


def test_invalid_nav_and_ter_are_rejected():
    with pytest.raises(MfReturnError, match="NAV values"):
        observed_nav_change(0, 100, start_date=date(2025, 1, 1),
                            end_date=date(2025, 2, 1))
    with pytest.raises(MfReturnError, match="TER"):
        forward_net_projection(12, 100)
