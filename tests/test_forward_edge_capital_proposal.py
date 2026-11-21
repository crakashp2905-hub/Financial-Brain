from pathlib import Path

from financial_brain.forward_edge.capital_proposal import (
    CapitalProposalError,
    CapitalRiskProposal,
    render_capital_proposal,
)

REPO = Path(__file__).resolve().parents[1]


def _proposal(**overrides):
    values = {"proposal_id": "micro-paper-v1", "account_alias": "owner-account",
              "strategy_id": "swing-v2", "review_owner": "Owner", "currency": "INR",
              "paper_capital_inr": 100_000, "proposed_micro_capital_inr": 10_000,
              "max_order_notional_inr": 2_000, "max_risk_per_trade_inr": 100,
              "max_daily_loss_inr": 300, "max_drawdown_inr": 1_000,
              "max_open_positions": 3, "max_orders_per_session": 5,
              "minimum_adv_notional_inr": 1_000_000, "maximum_spread_bps": 20,
              "estimated_round_trip_cost_inr": 5,
              "minimum_expected_gross_pnl_per_trade_inr": 100,
              "max_cost_to_expected_pnl_ratio": 0.10,
              "option_margin_verified": False, "physical_ordering_enabled": False}
    values.update(overrides)
    return CapitalRiskProposal(**values)


def test_micro_capital_proposal_is_review_only_and_explicitly_pending(tmp_path):
    proposal = _proposal()
    page = render_capital_proposal(proposal, tmp_path / "proposal.md").read_text(encoding="utf-8")
    assert len(proposal.digest()) == 64
    assert "PENDING HUMAN REVIEW" in page
    assert "Manual confirmation recorded: **NO**" in page
    assert "Maximum daily loss" in page
    assert "Maximum order notional" in page
    assert "not a capital approval or order instruction" in page
    template = (REPO / "docs/forward-edge/capital-risk-proposal-template.md").read_text(encoding="utf-8")
    assert "PENDING HUMAN REVIEW" in template
    assert "Maximum daily loss" in template
    assert "physical ordering is disabled" in template


def test_proposal_costs_must_fit_net_pnl_and_limits_must_be_nested():
    try:
        _proposal(estimated_round_trip_cost_inr=20)
    except CapitalProposalError as exc:
        assert "costs exceed" in str(exc)
    else:
        raise AssertionError("costs greater than proposed expected P&L should be refused")
    try:
        _proposal(max_daily_loss_inr=1_100)
    except CapitalProposalError as exc:
        assert "risk per trade <= daily loss <= max drawdown" in str(exc)
    else:
        raise AssertionError("risk limits should be nested")


def test_proposal_cannot_exceed_reviewed_capital_or_enable_physical_orders():
    for overrides, expected in (({"proposed_micro_capital_inr": 200_000}, "cannot exceed"),
                                ({"physical_ordering_enabled": True}, "disabled by build policy")):
        try:
            _proposal(**overrides)
        except CapitalProposalError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError(f"proposal must reject: {expected}")
from pathlib import Path

