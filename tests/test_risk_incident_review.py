from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from financial_brain.forward_edge.cockpit import CockpitError, risk_incident_review
from financial_brain.kernel.paper_account import PaperAccount, PaperAccountError


def test_active_paper_halt_is_visible_and_the_read_only_view_cannot_reset_it(tmp_path):
    journal_path = tmp_path / "paper-risk.jsonl"
    account = PaperAccount(journal_path, account_id="paper-risk", initial_cash=Decimal("1000"))
    account.trip_risk_latch(incident_id="incident-1", reason="DRAWDOWN_LIMIT",
                            occurred_at=datetime(2026, 11, 14, tzinfo=timezone.utc))
    html = risk_incident_review(journal_path)
    assert "HALT ACTIVE — PAPER INTENTS BLOCKED" in html
    assert "DRAWDOWN_LIMIT" in html and "read-only" in html
    assert "<form" not in html and "<button" not in html

    for actor in ("agent:codex", "gpt:helper", "automation:job"):
        with pytest.raises(PaperAccountError, match="human hash-backed"):
            account.reset_risk_latch(incident_id="incident-1", repair_id="repair-1", actor=actor,
                                     signed_at=datetime(2026, 11, 15, tzinfo=timezone.utc),
                                     signoff_sha256="a" * 64)
    assert account.risk_latched is True


def test_human_reset_is_audited_and_visible_after_journal_restart(tmp_path):
    journal_path = tmp_path / "paper-risk-reset.jsonl"
    account = PaperAccount(journal_path, account_id="paper-risk", initial_cash=Decimal("1000"))
    account.trip_risk_latch(incident_id="incident-2", reason="FEED_GAP",
                            occurred_at=datetime(2026, 11, 14, tzinfo=timezone.utc))
    account.reset_risk_latch(incident_id="incident-2", repair_id="repair-2", actor="owner:ramesh",
                             signed_at=datetime(2026, 11, 15, tzinfo=timezone.utc), signoff_sha256="b" * 64)
    reviewed = risk_incident_review(journal_path)
    assert "NO ACTIVE PAPER RISK LATCH" in reviewed
    assert "RESET BY HUMAN SIGN-OFF" in reviewed
    assert "owner:ramesh" in reviewed and "b" * 64 in reviewed


def test_corrupt_incident_journal_refuses_to_render(tmp_path):
    journal_path = tmp_path / "paper-risk-corrupt.jsonl"
    account = PaperAccount(journal_path, account_id="paper-risk", initial_cash=Decimal("1000"))
    account.trip_risk_latch(incident_id="incident-3", reason="LOSS_LIMIT",
                            occurred_at=datetime(2026, 11, 14, tzinfo=timezone.utc))
    data = journal_path.read_text(encoding="utf-8").replace("LOSS_LIMIT", "NO_LIMIT")
    journal_path.write_text(data, encoding="utf-8")
    with pytest.raises(CockpitError, match="corrupt"):
        risk_incident_review(journal_path)
