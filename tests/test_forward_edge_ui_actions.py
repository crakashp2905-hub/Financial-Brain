from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.approvals import Approval
from financial_brain.forward_edge.ui_actions import UIAction, authorize_ui_action


NOW = datetime(2026, 11, 16, 4, tzinfo=timezone.utc)
SCOPE = "a" * 64


@pytest.mark.parametrize("action", (UIAction.VIEW, UIAction.EXPORT, UIAction.ANALYZE))
def test_read_only_actions_are_allowed_without_approvals(action):
    result = authorize_ui_action(action, now=NOW)
    assert result.allowed
    assert result.read_only


@pytest.mark.parametrize("action", (UIAction.SUBMIT_ORDER, UIAction.CANCEL_ORDER))
def test_ui_can_never_submit_or_cancel_orders_even_with_live_approval(action):
    approval = Approval("LIVE_CASH_ENABLE", "NSE:ABC", SCOPE, "Ramesh", "human",
                        NOW - timedelta(minutes=1), NOW + timedelta(hours=1))
    result = authorize_ui_action(action, now=NOW, approvals=(approval,),
                                 subject="NSE:ABC", scope_sha256=SCOPE)
    assert not result.allowed
    assert result.read_only
    assert result.reason == "UI_ORDER_ROUTING_DISABLED_BY_BUILD_POLICY"


def test_staging_requires_exact_unexpired_human_approval():
    denied = authorize_ui_action(UIAction.STAGE_PAPER, now=NOW, subject="swing-v1",
                                 scope_sha256=SCOPE)
    assert not denied.allowed
    approval = Approval("STRATEGY_ENROLLMENT", "swing-v1", SCOPE, "Ramesh", "human",
                        NOW - timedelta(minutes=1), NOW + timedelta(hours=1))
    allowed = authorize_ui_action(UIAction.STAGE_PAPER, now=NOW, approvals=(approval,),
                                  subject="swing-v1", scope_sha256=SCOPE)
    assert allowed.allowed
    assert allowed.read_only  # staging approval does not submit an order
    stale_scope = authorize_ui_action(UIAction.STAGE_PAPER, now=NOW, approvals=(approval,),
                                      subject="swing-v1", scope_sha256="b" * 64)
    assert not stale_scope.allowed


def test_software_actor_cannot_authorize_live_staging():
    approval = Approval("LIVE_CASH_ENABLE", "account-1", SCOPE, "agent:pilot", "human",
                        NOW - timedelta(minutes=1), NOW + timedelta(hours=1))
    result = authorize_ui_action(UIAction.STAGE_LIVE, now=NOW, approvals=(approval,),
                                 subject="account-1", scope_sha256=SCOPE)
    assert not result.allowed
    assert "agent or non-human" in result.reason
