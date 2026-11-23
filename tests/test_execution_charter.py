from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.approvals import Approval
from financial_brain.forward_edge.capital_proposal import CapitalRiskProposal
from financial_brain.forward_edge.execution_charter import ExecutionCharter, ExecutionCharterError


def proposal(**changes):
    values = {
        "proposal_id": "paper-v1", "account_alias": "local-account", "strategy_id": "swing-v1",
        "review_owner": "owner-1", "currency": "INR", "paper_capital_inr": 100_000,
        "proposed_micro_capital_inr": 10_000, "max_order_notional_inr": 2_000,
        "max_risk_per_trade_inr": 100, "max_daily_loss_inr": 300, "max_drawdown_inr": 1_000,
        "max_open_positions": 3, "max_orders_per_session": 5, "minimum_adv_notional_inr": 1_000_000,
        "maximum_spread_bps": 20, "estimated_round_trip_cost_inr": 5,
        "minimum_expected_gross_pnl_per_trade_inr": 100, "max_cost_to_expected_pnl_ratio": 0.1,
        "option_margin_verified": False,
    }
    values.update(changes)
    return CapitalRiskProposal(**values)


def charter(**changes):
    values = {"charter_id": "paper-charter-v1", "owner_id": "owner-1", "execution_mode": "PAPER",
              "permitted_instruments": ("EQUITY", "ETF"), "capital_risk": proposal()}
    values.update(changes)
    return ExecutionCharter(**values)


def test_charter_digest_binds_mode_instruments_and_capital_limits():
    base = charter()
    assert len(base.sha256()) == 64
    assert charter(execution_mode="SHADOW").sha256() != base.sha256()
    assert charter(permitted_instruments=("EQUITY",)).sha256() != base.sha256()
    assert charter(capital_risk=proposal(max_daily_loss_inr=200)).sha256() != base.sha256()
    assert base.body()["physical_ordering_enabled"] is False


def test_charter_requires_human_approval_for_exact_digest():
    item = charter()
    now = datetime.now(timezone.utc)
    with pytest.raises(ExecutionCharterError, match="human CAPITAL approval"):
        item.require_human_approval(None, now=now)
    approval = Approval("CAPITAL", item.charter_id, item.sha256(), "owner-1", "human",
                        now - timedelta(minutes=1), now + timedelta(hours=1))
    item.require_human_approval(approval, now=now)
    stale = Approval("CAPITAL", item.charter_id, "0" * 64, "owner-1", "human",
                     now - timedelta(minutes=1), now + timedelta(hours=1))
    with pytest.raises(ExecutionCharterError, match="stale approval"):
        item.require_human_approval(stale, now=now)


def test_charter_rejects_live_mode_agents_and_owner_mismatch():
    with pytest.raises(ExecutionCharterError, match="only PAPER and SHADOW"):
        charter(execution_mode="LIVE")
    with pytest.raises(ExecutionCharterError, match="owner must match"):
        charter(owner_id="owner-2")
    item = charter()
    now = datetime.now(timezone.utc)
    approval = Approval("CAPITAL", item.charter_id, item.sha256(), "agent:planner", "human",
                        now - timedelta(minutes=1), now + timedelta(hours=1))
    with pytest.raises(ExecutionCharterError, match="non-human"):
        item.require_human_approval(approval, now=now)
