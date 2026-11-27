from __future__ import annotations

import hashlib
import json

from financial_brain.cli import main
from financial_brain.portfolio.canonical_ledger import CanonicalPortfolioLedger, EventKind, PortfolioEvent


ACCOUNT = "statement-test"
SOURCE = "kite-paper-import"
AT = "2026-11-20T10:00:00+05:30"
START = "2026-11-20T09:00:00+05:30"
END = "2026-11-20T15:30:00+05:30"
SOURCE_SHA = hashlib.sha256(b"statement source event").hexdigest()


def _ledger(path):
    ledger = CanonicalPortfolioLedger(path)
    ledger.append(PortfolioEvent(
        account_id=ACCOUNT, sleeve_id="equity", source_system=SOURCE,
        source_event_id="cash-1", source_sha256=SOURCE_SHA, event_kind=EventKind.CASH_FLOW,
        occurred_at=AT, cash_delta="10", metadata={"flow_type": "DISTRIBUTION"},
    ))
    return ledger


def _statement(amount: str = "10") -> dict[str, object]:
    return {
        "schema": "fb.portfolio-source-statement.v1",
        "account_id": ACCOUNT,
        "source_system": SOURCE,
        "period_start": START,
        "period_end": END,
        "events": [{
            "sleeve_id": "equity",
            "source_event_id": "cash-1",
            "source_sha256": SOURCE_SHA,
            "event_kind": "CASH_FLOW",
            "occurred_at": AT,
            "cash_delta": amount,
            "metadata": {"flow_type": "DISTRIBUTION"},
        }],
    }


def _write_statement(path, document: dict[str, object]) -> str:
    raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def test_cli_binds_report_to_exact_statement_bytes_and_persists_current_receipt(tmp_path, capsys):
    ledger_path = tmp_path / "ledger.db"
    _ledger(ledger_path)
    statement_path = tmp_path / "statement.json"
    source_document_hash = _write_statement(statement_path, _statement())

    result = main(["reconcile-source-events", "--ledger", str(ledger_path),
                   "--statement", str(statement_path)])

    report = json.loads(capsys.readouterr().out)
    assert result == 0
    assert report["reconciled"] is True
    assert report["registry_status"] == "CURRENT"
    assert report["source_document_sha256"] == source_document_hash
    assert report["matched_event_count"] == 1


def test_cli_returns_mismatch_report_and_nonzero_exit_for_changed_amount(tmp_path, capsys):
    ledger_path = tmp_path / "ledger.db"
    _ledger(ledger_path)
    statement_path = tmp_path / "statement.json"
    _write_statement(statement_path, _statement("11"))

    result = main(["reconcile-source-events", "--ledger", str(ledger_path),
                   "--statement", str(statement_path)])

    report = json.loads(capsys.readouterr().out)
    assert result == 2
    assert report["reconciled"] is False
    assert report["mismatched_event_ids"] == ["cash-1"]


def test_cli_rejects_floating_point_money(tmp_path, capsys):
    ledger_path = tmp_path / "ledger.db"
    _ledger(ledger_path)
    statement_path = tmp_path / "statement.json"
    document = _statement()
    document["events"][0]["cash_delta"] = 10.0
    _write_statement(statement_path, document)

    result = main(["reconcile-source-events", "--ledger", str(ledger_path),
                   "--statement", str(statement_path)])

    assert result == 2
    assert "exact decimal string" in capsys.readouterr().err
    assert json.loads(statement_path.read_text(encoding="utf-8")) == document


def test_cli_never_overwrites_the_source_statement(tmp_path, capsys):
    ledger_path = tmp_path / "ledger.db"
    _ledger(ledger_path)
    statement_path = tmp_path / "statement.json"
    document = _statement()
    _write_statement(statement_path, document)

    result = main(["reconcile-source-events", "--ledger", str(ledger_path),
                   "--statement", str(statement_path), "--output", str(statement_path)])

    assert result == 2
    assert "must not overwrite" in capsys.readouterr().err
    assert json.loads(statement_path.read_text(encoding="utf-8")) == document
