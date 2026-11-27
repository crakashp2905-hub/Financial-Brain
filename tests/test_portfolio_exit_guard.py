from decimal import Decimal

import pytest

from financial_brain.kernel.portfolio_exit_guard import (
    ExitGuardLimits,
    GuardPosition,
    PortfolioExitGuardError,
    evaluate_portfolio_exit_guard,
)


def _position(position_id, **changes):
    values = dict(
        position_id=position_id,
        instrument_id=f"NSE:{position_id}",
        asset_class="EQUITY",
        quantity="10",
        quantity_step="1",
        unit_stop_risk_inr="100",
        current_pnl_inr="0",
        risk_factor="BANKS",
        quote_executable=True,
        visible_exit_quantity="10",
    )
    values.update(changes)
    return GuardPosition(**values)


def test_aggregate_stop_risk_proposes_lot_aligned_reductions_without_orders():
    result = evaluate_portfolio_exit_guard(
        positions=(
            _position("A", quantity="10", quantity_step="5"),
            _position("B", quantity="4", quantity_step="2"),
        ),
        limits=ExitGuardLimits(max_open_stop_risk_inr="700"),
    )
    by_id = {row.position_id: row for row in result.proposals}
    assert result.total_open_stop_risk_inr == Decimal("1400")
    assert by_id["A"].requested_quantity == Decimal("5")
    assert by_id["B"].requested_quantity == Decimal("2")
    assert all(row.action == "PAPER_EXIT_REVIEW" for row in result.proposals)
    assert result.physical_orders_submitted is False


def test_correlated_loss_only_reduces_losing_members_of_breached_factor():
    result = evaluate_portfolio_exit_guard(
        positions=(
            _position("A", current_pnl_inr="-800", risk_factor="BANKS"),
            _position("B", current_pnl_inr="-400", risk_factor="BANKS"),
            _position("C", current_pnl_inr="500", risk_factor="IT"),
        ),
        limits=ExitGuardLimits(max_open_stop_risk_inr="10000",
                               max_correlated_loss_inr_by_factor={"BANKS": "600"}),
    )
    assert result.correlated_loss_inr_by_factor == {"BANKS": Decimal("1200"), "IT": Decimal(0)}
    assert {row.position_id for row in result.proposals} == {"A", "B"}
    assert all("CORRELATED_LOSS_LIMIT:BANKS" in row.reason_codes for row in result.proposals)


def test_option_greek_breach_scales_option_positions_and_respects_visible_depth():
    result = evaluate_portfolio_exit_guard(
        positions=(
            _position("OPT-A", instrument_id="NFO:A", asset_class="OPTION", quantity="4",
                      option_delta_exposure_inr="800", quote_executable=True, visible_exit_quantity="4"),
            _position("OPT-B", instrument_id="NFO:B", asset_class="OPTION", quantity="4",
                      option_delta_exposure_inr="200", quote_executable=True, visible_exit_quantity="1"),
            _position("EQ", option_delta_exposure_inr="0"),
        ),
        limits=ExitGuardLimits(max_open_stop_risk_inr="10000", max_gross_option_delta_inr="500"),
    )
    by_id = {row.position_id: row for row in result.proposals}
    assert result.gross_option_exposure_inr["delta"] == Decimal("1000")
    assert by_id["OPT-A"].requested_quantity == Decimal("2")
    assert by_id["OPT-A"].executable_quantity == Decimal("2")
    assert by_id["OPT-B"].requested_quantity == Decimal("2")
    assert by_id["OPT-B"].executable_quantity == Decimal("1")
    assert by_id["OPT-B"].status == "DEPTH_LIMITED"
    assert "OPTION_DELTA_LIMIT" in by_id["OPT-B"].reason_codes
    assert "VISIBLE_DEPTH_LIMIT" in by_id["OPT-B"].reason_codes
    assert "EQ" not in by_id


def test_mf_risk_reduction_is_a_nav_review_not_an_intraday_fill():
    result = evaluate_portfolio_exit_guard(
        positions=(_position("MF", instrument_id="INF000000001", asset_class="MUTUAL_FUND",
                             quantity="10.500", quantity_step="0.001", current_pnl_inr="-800",
                             quote_executable=False),),
        limits=ExitGuardLimits(max_open_stop_risk_inr="500"),
    )
    proposal = result.proposals[0]
    assert proposal.action == "NAV_REDEMPTION_REVIEW"
    assert proposal.status == "REVIEW_CUTOFF_AND_SETTLEMENT"
    assert proposal.requested_quantity == Decimal("5.500")
    assert proposal.executable_quantity == 0
    assert "NAV_BASED_EXECUTION_ONLY" in proposal.reason_codes


def test_futures_positions_are_eligible_for_exchange_exit_risk_reduction():
    result = evaluate_portfolio_exit_guard(
        positions=(_position("NIFTY-FUT", instrument_id="NFO:NIFTY-FUT", asset_class="FUTURE",
                             quantity="50", quantity_step="25", current_pnl_inr="-900",
                             visible_exit_quantity="50"),),
        limits=ExitGuardLimits(max_open_stop_risk_inr="2500"),
    )

    proposal = result.proposals[0]
    assert proposal.asset_class == "FUTURE"
    assert proposal.action == "PAPER_EXIT_REVIEW"
    assert proposal.requested_quantity == Decimal("25")


def test_missing_quote_and_depth_fail_closed_for_exchange_exits():
    no_quote = evaluate_portfolio_exit_guard(
        positions=(_position("A", quote_executable=False, visible_exit_quantity="10"),),
        limits=ExitGuardLimits(max_open_stop_risk_inr="500"),
    ).proposals[0]
    no_depth = evaluate_portfolio_exit_guard(
        positions=(_position("B", visible_exit_quantity="0"),),
        limits=ExitGuardLimits(max_open_stop_risk_inr="500"),
    ).proposals[0]
    assert no_quote.status == "NO_EXECUTABLE_QUOTE" and no_quote.executable_quantity == 0
    assert no_depth.status == "NO_VISIBLE_DEPTH" and no_depth.executable_quantity == 0


def test_unbreached_portfolio_emits_no_exit_proposal():
    result = evaluate_portfolio_exit_guard(
        positions=(_position("A", current_pnl_inr="100"),),
        limits=ExitGuardLimits(max_open_stop_risk_inr="2000",
                               max_correlated_loss_inr_by_factor={"BANKS": "1000"}),
    )
    assert result.proposals == ()
    assert result.physical_orders_submitted is False


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"quantity": "0"}, "quantity/step must be positive"),
        ({"quantity": "3", "quantity_step": "2"}, "must align"),
        ({"asset_class": "OTHER"}, "unsupported asset class"),
        ({"asset_class": "ETF", "option_vega_exposure_inr": "1"}, "only valid for option"),
    ],
)
def test_invalid_position_identity_or_lot_data_is_rejected(changes, message):
    position = _position("BAD", **changes)
    with pytest.raises(PortfolioExitGuardError, match=message):
        evaluate_portfolio_exit_guard(
            positions=(position,), limits=ExitGuardLimits(max_open_stop_risk_inr="1000"))
