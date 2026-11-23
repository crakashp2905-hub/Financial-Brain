from datetime import date

import pytest

from financial_brain.mf_exit_load import (ExitLoadBand, ExitLoadError, PurchaseLot,
                                          estimate_redemption)

HASH = "c" * 64


def test_partial_and_full_redemption_apply_each_lots_own_age_band():
    lots = [PurchaseLot("old", 10, date(2025, 1, 1), True,
                        (ExitLoadBand(365, 1.0),), HASH),
            PurchaseLot("new", 5, date(2026, 11, 1), True,
                        (ExitLoadBand(30, 2.0),), HASH)]
    partial = estimate_redemption(lots, 12, nav=100, redemption_on=date(2026, 11, 23))
    assert partial["redemption_type"] == "PARTIAL"
    assert [(x["lot_id"], x["units"], x["exit_load_rate_pct"]) for x in partial["allocations"]] == [
        ("old", 10, 0.0), ("new", 2, 2.0)]
    assert partial["exit_load_inr"] == 4
    full = estimate_redemption(lots, 15, nav=100, redemption_on=date(2026, 11, 23))
    assert full["redemption_type"] == "FULL"


def test_unknown_schedule_is_not_treated_as_zero_and_overredemption_fails():
    lot = PurchaseLot("lot", 3, date(2026, 11, 1), False)
    result = estimate_redemption([lot], 1, nav=10, redemption_on=date(2026, 11, 3))
    assert result["exit_load_inr"] is None
    with pytest.raises(ExitLoadError):
        estimate_redemption([lot], 4, nav=10, redemption_on=date(2026, 11, 3))


def test_known_schedule_requires_source_and_terms_validate():
    with pytest.raises(ExitLoadError):
        PurchaseLot("lot", 1, date(2026, 1, 1), True)
    with pytest.raises(ExitLoadError):
        ExitLoadBand(0, 1)
