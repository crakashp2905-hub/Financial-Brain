from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from financial_brain.paper.options import Quote
from financial_brain.paper.option_ranker import (
    OptionRankCandidate,
    rank_long_option_contracts,
)


AS_OF = datetime(2026, 11, 22, 5, tzinfo=timezone.utc)


def _candidate(candidate_id, right, *, ask=102, ask_qty=500, expected_exit=140):
    quote = Quote(123 if right == "CE" else 124,
                  f"NIFTY26NOV25000{right}", date(2026, 11, 26), 25,
                  AS_OF - timedelta(seconds=2), 100, ask, 500, ask_qty)
    return OptionRankCandidate(candidate_id, "NIFTY", right, 25_000, quote,
                               0.25, expected_exit, 4 / 365)


def test_ranker_reports_executable_quote_greeks_risk_size_and_selection():
    result = rank_long_option_contracts(
        candidates=(_candidate("call", "CE"),
                    _candidate("put", "PE", expected_exit=110)),
        underlying_price=25_100, as_of=AS_OF, risk_budget_inr=10_000,
        max_quote_age_seconds=5, max_spread_bps=500,
    )
    assert result["selected_candidate_id"] == "call"
    call, put = result["candidates"]
    assert call["status"] == "SELECTED" and put["status"] == "NOT_SELECTED"
    assert call["entry_price_reference"] == "OBSERVED_ASK"
    assert call["expected_exit_bid_is_observed"] is False
    assert call["break_even_price"] == 25_102
    assert call["recommended_lots"] > 0
    assert call["maximum_loss_inr"] > 0
    assert call["greeks"]["gamma"] > 0
    assert call["gamma_exposure_per_lot"] > 0
    assert call["theta_exposure_per_day_per_lot"] < 0
    assert len(result["report_sha256"]) == 64


def test_ranker_explains_no_selection_for_spread_depth_and_risk_failures():
    result = rank_long_option_contracts(
        candidates=(_candidate("wide", "CE", ask=103),
                    _candidate("thin", "PE", ask_qty=10)),
        underlying_price=25_100, as_of=AS_OF, risk_budget_inr=1,
        max_quote_age_seconds=5, max_spread_bps=100,
    )
    assert result["selected_candidate_id"] is None
    assert result["selected_action"] == "CASH_NO_ACTION"
    assert result["cash_no_action_baseline"]["expected_net_pnl_inr"] == 0
    assert result["selection_reason"] == "NO_CANDIDATE_CLEARED_ALL_GATES"
    reasons = {row["candidate_id"]: set(row["reason_codes"]) for row in result["candidates"]}
    assert "SPREAD_EXCEEDS_POLICY" in reasons["wide"]
    assert "RISK_BUDGET_BELOW_ONE_LOT" in reasons["thin"]
    assert "INSUFFICIENT_ASK_DEPTH" in reasons["thin"]


def test_near_expiry_and_spread_or_tail_cost_can_block_even_high_expected_reward():
    base = _candidate("near-expiry", "CE")
    unmeasured = replace(base, minutes_to_expiry=30)
    tail_exceeds_alpha = replace(base, candidate_id="tail-risk", minutes_to_expiry=30,
                                 stress_tail_loss_per_lot_inr=2_000)
    wide_quote = Quote(125, "NIFTY26NOV25000CE", date(2026, 11, 26), 25,
                       AS_OF - timedelta(seconds=1), 50, 102, 500, 500)
    wide = replace(base, candidate_id="wide-near-expiry", quote=wide_quote,
                   minutes_to_expiry=30, stress_tail_loss_per_lot_inr=0)
    result = rank_long_option_contracts(
        candidates=(unmeasured, tail_exceeds_alpha, wide),
        underlying_price=25_100, as_of=AS_OF, risk_budget_inr=10_000,
        max_quote_age_seconds=5, max_spread_bps=20_000,
    )
    reasons = {row["candidate_id"]: set(row["reason_codes"]) for row in result["candidates"]}
    assert "NEAR_EXPIRY_TAIL_RISK_UNMEASURED" in reasons["near-expiry"]
    assert "TAIL_GAP_EXCEEDS_EXPECTED_ALPHA" in reasons["tail-risk"]
    assert "SPREAD_COST_EXCEEDS_EXPECTED_ALPHA" in reasons["wide-near-expiry"]
    assert result["selected_candidate_id"] is None


def test_ranker_reports_delta_equivalent_cash_comparison_without_claiming_calibration():
    candidate = replace(_candidate("call", "CE"),
                        expected_underlying_exit_price=25_200)
    result = rank_long_option_contracts(
        candidates=(candidate,), underlying_price=25_100, as_of=AS_OF,
        risk_budget_inr=10_000, max_quote_age_seconds=5, max_spread_bps=500,
    )
    benchmark = result["candidates"][0]["delta_equivalent_benchmark"]
    assert benchmark["underlying_units_per_lot"] > 0
    assert benchmark["gross_pnl_per_lot_inr_before_underlying_costs"] > 0
    assert benchmark["calibrated"] is False
    assert result["cash_no_action_baseline"]["always_available"] is True
