from __future__ import annotations

from argparse import Namespace
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from financial_brain.config import Config
from financial_brain.paper.mf_decisions import (
    MFAction,
    MFActionDecision,
    MFActionDecisionError,
    decisions_as_of,
    record_decision,
)
from financial_brain.paper.mf_lot_book import create_account
from financial_brain.storage.db import Database

NOW = datetime(2026, 11, 24, 10, tzinfo=timezone.utc)
SNAPSHOT = "a" * 64


@pytest.fixture
def connection(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    with db.connect() as con:
        create_account(con, "decision-account", 50_000, now=NOW)
        yield con


def _decision(action: MFAction = MFAction.SUBSCRIBE, **updates) -> MFActionDecision:
    values = dict(
        decision_id="mf-choice-1", account_id="decision-account", actor_id="owner",
        action=action, reason="owner-reviewed point-in-time context",
        source_snapshot_sha256=SNAPSHOT, decided_at=NOW,
        expires_at=NOW + timedelta(days=1), scheme_code="100001", plan="DIRECT",
        option="GROWTH", amount_inr=Decimal("5000"),
    )
    if action is MFAction.REDEEM:
        values.update(folio_id="paper-folio", amount_inr=None, units=Decimal("20"))
    elif action is MFAction.SWITCH:
        values.update(folio_id="paper-folio", units=Decimal("20"), amount_inr=None,
                      target_scheme_code="100002", target_plan="DIRECT", target_option="GROWTH")
    elif action is MFAction.SIP:
        values.update(sip_frequency="MONTHLY", sip_start=date(2026, 12, 1),
                      sip_end=date(2027, 12, 1))
    elif action is MFAction.HOLD:
        values.update(amount_inr=None)
    values.update(updates)
    return MFActionDecision(**values)


@pytest.mark.parametrize("action", list(MFAction))
def test_all_five_mf_choices_are_paper_decisions_not_orders(connection, action):
    row = _decision(action)
    assert record_decision(connection, row, recorded_at=NOW + timedelta(seconds=1))
    visible = decisions_as_of(connection, account_id=row.account_id, known_at=NOW + timedelta(minutes=1))
    assert visible[0]["action"] == action.value
    assert visible[0]["status"] == "CURRENT"
    body = __import__("json").loads(visible[0]["body_json"])
    assert body["execution_scope"] == "PAPER_DECISION_ONLY"
    assert body["physical_orders_authorized"] is False
    assert connection.execute("SELECT COUNT(*) FROM mf_paper_orders").fetchone()[0] == 0


def test_journal_is_idempotent_hashed_and_conflict_detecting(connection):
    row = _decision()
    assert record_decision(connection, row, recorded_at=NOW)
    assert not record_decision(connection, row, recorded_at=NOW + timedelta(seconds=1))
    with pytest.raises(MFActionDecisionError, match="different content"):
        record_decision(connection, _decision(reason="changed"), recorded_at=NOW)
    assert connection.execute("SELECT COUNT(*) FROM mf_action_decisions").fetchone()[0] == 1


def test_invalid_expired_or_wrong_action_shapes_are_rejected():
    with pytest.raises(MFActionDecisionError, match="REDEEM requires"):
        _decision(MFAction.REDEEM, folio_id="folio", units=None)
    with pytest.raises(MFActionDecisionError, match="expiry"):
        _decision(expires_at=NOW)
    with pytest.raises(MFActionDecisionError, match="timezone aware"):
        _decision(decided_at=datetime(2026, 11, 24, 10))


def test_expired_decision_is_reported_as_expired(connection):
    row = _decision(expires_at=NOW + timedelta(minutes=1))
    record_decision(connection, row, recorded_at=NOW)
    visible = decisions_as_of(connection, account_id=row.account_id, known_at=NOW + timedelta(minutes=2))
    assert visible[0]["status"] == "EXPIRED"


def test_cli_records_choice_and_terminal_surfaces_decision_language(connection, monkeypatch, capsys):
    from financial_brain import cli
    from financial_brain.paper.terminal import mf_paper_panel

    monkeypatch.setattr(cli, "load", lambda: object())
    monkeypatch.setattr(cli, "Database", lambda _: SimpleNamespace(
        migrate=lambda: None, connect=lambda: nullcontext(connection),
    ))
    cli_decided_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    args = Namespace(
        decision_id="mf-cli-1", account_id="decision-account", actor_id="owner",
        action="HOLD", reason="retain emergency cash", snapshot_sha256=SNAPSHOT,
        decided_at=cli_decided_at.isoformat(), expires_at=(cli_decided_at + timedelta(days=1)).isoformat(),
        scheme_code="100001", plan="DIRECT", option="GROWTH", folio_id=None,
        amount_inr=None, units=None, target_scheme_code=None, target_plan=None,
        target_option=None, sip_frequency=None, sip_start=None, sip_end=None,
    )
    assert cli.cmd_mf_decision(args) == 0
    import json
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["decision"]["action"] == "HOLD"
    assert out["physical_orders_authorized"] is False
    panel_date = (cli_decided_at + timedelta(hours=5, minutes=30)).date()
    panel = mf_paper_panel(connection, panel_date)
    for action in ("SUBSCRIBE", "REDEEM", "SWITCH", "SIP", "HOLD"):
        assert action in panel
    assert "MF actions below are decisions, not current-price orders" in panel
    assert "no paper order or external transaction was created" in panel
