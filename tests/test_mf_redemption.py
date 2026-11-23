from datetime import date

from financial_brain.mf_redemption import RedemptionLot, RedemptionTerms, check_redemption

DAY = date(2026, 11, 23)


def terms(**kwargs):
    defaults = {"scheme_status": "ACTIVE", "status_as_of": DAY,
                "redemption_window_open": True, "gating_max_units": 100}
    return RedemptionTerms(**(defaults | kwargs))


def test_elss_locked_lot_and_gating_reject_impossible_redemption():
    locked = check_redemption(terms=terms(), lots=[RedemptionLot(10, date(2027, 1, 1))],
                              units=5, as_of=DAY)
    assert locked["verdict"] == "REJECT"
    assert "ELSS_OR_OTHER_LOT_LOCK_IN_ACTIVE" in locked["reasons"]
    gated = check_redemption(terms=terms(gating_max_units=2), lots=[RedemptionLot(10)],
                             units=5, as_of=DAY)
    assert "REDEMPTION_EXCEEDS_GATING_LIMIT" in gated["reasons"]


def test_segregated_units_closed_end_and_closed_window_are_enforced():
    result = check_redemption(terms=terms(closed_end_maturity=date(2027, 1, 1),
                                          redemption_window_open=False, segregated_units=3),
                              lots=[RedemptionLot(10)], units=8, as_of=DAY)
    assert result["verdict"] == "REJECT"
    assert "CLOSED_END_SCHEME_NOT_MATURED" in result["reasons"]
    assert "REDEMPTION_WINDOW_CLOSED" in result["reasons"]
    assert "REQUEST_EXCEEDS_REDEEMABLE_UNITS" in result["reasons"]


def test_missing_restriction_evidence_requires_review():
    result = check_redemption(terms=RedemptionTerms("ACTIVE", DAY),
                             lots=[RedemptionLot(1)], units=1, as_of=DAY)
    assert result["verdict"] == "REVIEW_REQUIRED"
    assert "REDEMPTION_WINDOW_UNKNOWN" in result["unknown_facts"]
    assert "GATING_LIMIT_UNKNOWN" in result["unknown_facts"]
