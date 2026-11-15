from datetime import datetime, timedelta
import json
import socket

import pytest

from financial_brain.cli import main
from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.offline_paper import _events_from_archive
from financial_brain.forward_edge.session_runner import ForwardSessionRunner


DAY = "2026-11-12"
EVENT = {
    "kind": "QUOTE", "instrument": "NSE:INFY",
    "event_utc": "2026-11-12T04:00:00+00:00",
    "received_utc": "2026-11-12T04:00:00.010000+00:00",
    "available_utc": "2026-11-12T04:00:00.020000+00:00",
    "payload": {"last_price": "1500", "source": "kite_connect_archive", "exchange": "NSE"},
}


def _archive(path):
    path.write_text(json.dumps({"schema_version": 1, "exchange_date": DAY, "events": [EVENT]}),
                    encoding="utf-8")
    return path


def _args(archive, journal):
    return ["forward", "real-paper", "--date", DAY, "--cohort", "offline-test",
            "--archive", str(archive), "--journal", str(journal), "--confirm-paper-only"]


def _deny_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("offline archived replay attempted network access")
    monkeypatch.setattr(socket, "socket", denied)


def test_real_paper_archive_cli_runs_without_network_or_physical_orders(tmp_path, monkeypatch, capsys):
    archive = _archive(tmp_path / "session.json")
    journal = tmp_path / "paper.jsonl"
    _deny_network(monkeypatch)

    assert main(_args(archive, journal)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "NO_TRADE"
    assert result["mode"] == "RESEARCH_REPLAY"
    assert result["evidence_tier"] == "HISTORICAL_REPLAY"
    assert result["network_access"] is False
    assert result["physical_orders"] is False
    records = DurableJournal(journal).records()
    assert sum(row["t"] == "MARKET_EVENT" for row in records) == 1
    assert sum(row["t"] == "SESSION_CLOSED" for row in records) == 1
    assert not any(row["t"] in {"PAPER_INTENT", "PAPER_EXECUTION"} for row in records)

    # Re-run of a closed session and explicit recovery return the frozen result without new events.
    record_count = len(records)
    assert main(_args(archive, journal)) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "NO_TRADE"
    assert main(["forward", "session", "--resume", f"{DAY}:offline-test:attempt-1",
                 "--journal", str(journal)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "NO_TRADE"
    assert len(DurableJournal(journal).records()) == record_count


def test_resume_after_interrupted_session_reuses_journal_events_and_never_duplicates_fill(
    tmp_path, monkeypatch, capsys
):
    archive = _archive(tmp_path / "session.json")
    journal = tmp_path / "paper.jsonl"
    original_run = ForwardSessionRunner.run

    def crash_after_durable_capture(self, *args, **kwargs):
        raise RuntimeError("injected process crash")

    monkeypatch.setattr(ForwardSessionRunner, "run", crash_after_durable_capture)
    with pytest.raises(RuntimeError, match="injected process crash"):
        main(_args(archive, journal))
    before = DurableJournal(journal).records()
    assert any(row["t"] == "MARKET_EVENT" for row in before)
    assert not any(row["t"] in {"PAPER_INTENT", "PAPER_EXECUTION"} for row in before)

    monkeypatch.setattr(ForwardSessionRunner, "run", original_run)
    _deny_network(monkeypatch)
    assert main(["forward", "session", "--resume", f"{DAY}:offline-test:attempt-1",
                 "--journal", str(journal)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "NO_TRADE"
    after = DurableJournal(journal).records()
    assert sum(row["t"] == "MARKET_EVENT" for row in after) == 1
    assert sum(row["t"] == "SESSION_CLOSED" for row in after) == 1
    assert not any(row["t"] in {"PAPER_INTENT", "PAPER_EXECUTION"} for row in after)


def test_archive_requires_same_exchange_date_timezone_and_source_lineage(tmp_path):
    path = _archive(tmp_path / "session.json")
    events, digest = _events_from_archive(path, DAY)
    assert len(events) == 1 and len(digest) == 64

    payload = json.loads(path.read_text())
    payload["events"][0]["event_utc"] = (datetime.fromisoformat(EVENT["event_utc"])
                                            + timedelta(days=1)).isoformat()
    payload["events"][0]["received_utc"] = (datetime.fromisoformat(EVENT["received_utc"])
                                               + timedelta(days=1)).isoformat()
    payload["events"][0]["available_utc"] = (datetime.fromisoformat(EVENT["available_utc"])
                                                + timedelta(days=1)).isoformat()
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="outside the requested exchange date"):
        _events_from_archive(path, DAY)


def test_real_paper_cli_requires_explicit_paper_confirmation(tmp_path, capsys):
    archive = _archive(tmp_path / "session.json")
    journal = tmp_path / "paper.jsonl"
    result = main([arg for arg in _args(archive, journal) if arg != "--confirm-paper-only"])
    assert result == 2
    assert json.loads(capsys.readouterr().out)["status"] == "REJECTED"
    assert not journal.exists()

