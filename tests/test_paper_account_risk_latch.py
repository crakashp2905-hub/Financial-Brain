from datetime import datetime, timezone
from decimal import Decimal

import pytest

from financial_brain.kernel.oms import State
from financial_brain.kernel.paper_account import PaperAccount, PaperAccountError


def test_risk_latch_and_open_order_reservations_survive_restart_and_require_human_reset(tmp_path):
    path = tmp_path / "risk-latch.jsonl"
    account = PaperAccount(path, account_id="risk", initial_cash=Decimal("1000"))
    account.submit_intent(client_id="open-1", symbol="NSE:INFY", side="BUY", quantity=5,
                          limit=Decimal("100"), reserve=Decimal("500"))
    account.move("open-1", State.SUBMITTED)
    account.move("open-1", State.ACCEPTED)
    assert account.available_cash == Decimal("500")
    assert account.trip_risk_latch(incident_id="inc-1", reason="STALE_FEED",
                                   occurred_at=datetime(2026, 11, 12, 10, tzinfo=timezone.utc))

    restarted = PaperAccount(path, account_id="risk", initial_cash=Decimal("1000"))
    assert restarted.risk_latched is True
    assert restarted.oms.order("open-1").state is State.ACCEPTED
    assert restarted.available_cash == Decimal("500")
    with pytest.raises(PaperAccountError, match="risk latch blocks"):
        restarted.submit_intent(client_id="open-2", symbol="NSE:TCS", side="BUY", quantity=1,
                                limit=Decimal("100"), reserve=Decimal("100"))
    with pytest.raises(PaperAccountError, match="human hash-backed"):
        restarted.reset_risk_latch(incident_id="inc-1", repair_id="repair-1", actor="agent:codex",
                                   signed_at=datetime(2026, 11, 13, 10, tzinfo=timezone.utc), signoff_sha256="a" * 64)

    restarted.reset_risk_latch(incident_id="inc-1", repair_id="repair-1", actor="owner",
                               signed_at=datetime(2026, 11, 13, 10, tzinfo=timezone.utc), signoff_sha256="b" * 64)
    after_reset = PaperAccount(path, account_id="risk", initial_cash=Decimal("1000"))
    assert after_reset.risk_latched is False
    assert after_reset.oms.order("open-1").state is State.ACCEPTED
    assert after_reset.available_cash == Decimal("500")


def test_latch_does_not_allow_signoff_to_predate_incident(tmp_path):
    path = tmp_path / "signoff-time.jsonl"
    account = PaperAccount(path, account_id="risk", initial_cash=Decimal("1000"))
    account.trip_risk_latch(incident_id="inc-2", reason="RISK_BREACH",
                            occurred_at=datetime(2026, 11, 12, 10, tzinfo=timezone.utc))
    with pytest.raises(PaperAccountError, match="predates"):
        account.reset_risk_latch(incident_id="inc-2", repair_id="repair-2", actor="owner",
                                 signed_at=datetime(2026, 11, 12, 9, tzinfo=timezone.utc), signoff_sha256="c" * 64)
