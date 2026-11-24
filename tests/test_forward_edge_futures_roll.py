"""FE-0063 futures roll basis, lot, margin, financing and MTM calculations."""
from datetime import date

import pytest

from financial_brain.forward_edge.futures_roll import FuturesContractMark, FuturesRollError, roll_futures_position


def _marks(old_price=100, new_price=99, new_lot=25):
    day = date(2026, 11, 15)
    return (FuturesContractMark("NIFTY26NOVFUT", date(2026, 11, 26), 50, old_price, day, 180_000),
            FuturesContractMark("NIFTY26DECFUT", date(2026, 12, 31), new_lot, new_price, day, 170_000))


def test_roll_preserves_negative_basis_and_reconciles_lot_margin_financing_and_mtm() -> None:
    old, new = _marks()
    report = roll_futures_position(old=old, new=new, lots=2, prior_settlement=98,
                                  financing_rate_annual=0.06, calendar_days=10)
    assert report["basis_old_to_new"] == -1
    assert report["basis_sign"] == "NEGATIVE"
    assert report["old_quantity"] == 100 and report["new_quantity"] == 50
    assert report["quantity_delta"] == -50
    assert report["pre_roll_mtm_inr"] == 200
    assert report["margin_change_inr"] == -20_000
    assert report["financing_cost_inr"] == pytest.approx(100 * 100 * 0.06 * 10 / 365)
    assert report["mark_source_status"] == "CALLER_SUPPLIED_NOT_AUTHENTICATED"


def test_roll_rejects_future_expiry_or_asynchronous_marks() -> None:
    old, new = _marks()
    with pytest.raises(FuturesRollError, match="share an observation"):
        roll_futures_position(old=old, new=FuturesContractMark(**{**new.__dict__,
                            "observed_on": date(2026, 11, 16)}), lots=1, prior_settlement=99,
                            financing_rate_annual=0.05, calendar_days=1)
    with pytest.raises(FuturesRollError, match="distinct"):
        roll_futures_position(old=old, new=FuturesContractMark(**{**new.__dict__, "symbol": old.symbol}),
                              lots=1, prior_settlement=99, financing_rate_annual=0.05, calendar_days=1)
