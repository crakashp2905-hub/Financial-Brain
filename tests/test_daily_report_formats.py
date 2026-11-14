from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import csv
from html.parser import HTMLParser
import io
import json
from pathlib import Path

import pytest

from financial_brain.forward_edge.daily_report import (
    DailyReportError,
    build_daily_report,
    render_daily_reports,
    write_daily_reports,
)
from financial_brain.forward_edge import cli
from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.eod_paper_archive import close_and_archive_paper_session
from financial_brain.kernel.oms import State
from financial_brain.kernel.paper_account import PaperAccount


IST = timezone(timedelta(hours=5, minutes=30))
SESSION_DATE = date(2026, 11, 14)
SESSION_ID = "paper-2026-11-14"


class _ReportParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = {}
        self.active_field = None

    def handle_starttag(self, tag, attrs):
        if tag == "td":
            self.active_field = dict(attrs).get("data-field")

    def handle_data(self, data):
        if self.active_field:
            self.fields[self.active_field] = data

    def handle_endtag(self, tag):
        if tag == "td":
            self.active_field = None


def _artifacts(tmp_path):
    account = PaperAccount(tmp_path / "account.jsonl", account_id="daily-report",
                           initial_cash=Decimal("10000"))
    account.submit_intent(client_id="buy-1", symbol="NSE:INFY", side="BUY", quantity=3,
                          limit=Decimal("100"), reserve=Decimal("300"))
    account.move("buy-1", State.SUBMITTED)
    account.move("buy-1", State.ACCEPTED)
    account.record_fill("buy-1", fill_id="fill-1", quantity=3, price=Decimal("100"))
    archive = close_and_archive_paper_session(
        account=account, session_date=SESSION_DATE,
        captured_at=datetime(2026, 11, 14, 15, 25, tzinfo=IST),
        marks={"NSE:INFY": {"price": "95", "observed_at": datetime(
            2026, 11, 14, 15, 20, tzinfo=IST).isoformat(), "source_id": "fixture-close",
            "source_sha256": "a" * 64}}, archive_dir=tmp_path / "archives")
    runner_path = tmp_path / "runner.jsonl"
    journal = DurableJournal(runner_path)
    records = [
        {"t": "SESSION_PLAN", "session_id": SESSION_ID,
         "config": {"exchange_date": SESSION_DATE.isoformat()}},
        {"t": "MARKET_EVENT", "session_id": SESSION_ID},
        {"t": "DECISION", "session_id": SESSION_ID},
        {"t": "PAPER_INTENT", "session_id": SESSION_ID},
        {"t": "PAPER_EXECUTION", "session_id": SESSION_ID, "fidelity": "SIMULATED_QUOTE"},
        {"t": "PAPER_ORDER_UPDATE", "session_id": SESSION_ID},
        {"t": "PAPER_SKIP", "session_id": SESSION_ID},
        {"t": "SESSION_CLOSED", "session_id": SESSION_ID, "result": {"status": "CLOSED"}},
    ]
    journal.append_many(records)
    account_archive = json.loads(Path(archive["archive_path"]).read_text(encoding="utf-8"))
    return account_archive, runner_path


def test_json_csv_html_share_the_same_versioned_totals(tmp_path):
    archive, runner = _artifacts(tmp_path)
    report = build_daily_report(account_archive=archive, runner_journal_path=runner, session_id=SESSION_ID)
    artifacts = render_daily_reports(report)
    loaded_json = json.loads(artifacts["report.json"])
    csv_row = next(csv.DictReader(io.StringIO(artifacts["report.csv"].decode("utf-8"))))
    parser = _ReportParser()
    parser.feed(artifacts["report.html"].decode("utf-8"))

    assert loaded_json["schema_version"] == "1.0"
    for name, value in loaded_json["totals"].items():
        assert csv_row[name] == str(value)
        assert parser.fields[name] == str(value)
    assert loaded_json["outcome"] == csv_row["outcome"] == parser.fields["outcome"] == "CLOSED"
    assert loaded_json["totals"]["equity_inr"] == "9985"
    assert loaded_json["totals"]["open_position_market_value_inr"] == "285"
    assert loaded_json["totals"]["event_decision_count"] == 1
    assert b"\r" not in artifacts["report.csv"]
    assert artifacts == render_daily_reports(report)

    paths = write_daily_reports(report, tmp_path / "exports")
    assert set(paths) == {"report.json", "report.csv", "report.html"}
    assert paths["report.json"].read_bytes() == artifacts["report.json"]


def test_daily_export_refuses_tampered_schema_archive_or_journal(tmp_path):
    archive, runner = _artifacts(tmp_path)
    archive["equity"] = "1"
    with pytest.raises(DailyReportError, match="archive hash"):
        build_daily_report(account_archive=archive, runner_journal_path=runner, session_id=SESSION_ID)

    good_archive, runner = _artifacts(tmp_path / "other")
    report = build_daily_report(account_archive=good_archive, runner_journal_path=runner, session_id=SESSION_ID)
    report["totals"]["equity_inr"] = "1"
    with pytest.raises(DailyReportError, match="report hash"):
        render_daily_reports(report)


def test_daily_export_requires_session_date_to_match_the_archive(tmp_path):
    archive, runner = _artifacts(tmp_path)
    with pytest.raises(DailyReportError, match="session plan"):
        build_daily_report(account_archive=archive, runner_journal_path=runner, session_id="other-session")


def test_daily_export_cli_reports_all_three_sibling_artifacts(tmp_path, monkeypatch, capsys):
    archive_path = tmp_path / "account.json"
    archive_path.write_text("{}", encoding="utf-8")
    report = {"report_sha256": "a" * 64}
    monkeypatch.setattr("financial_brain.forward_edge.daily_report.build_daily_report",
                        lambda **_kwargs: report)
    monkeypatch.setattr("financial_brain.forward_edge.daily_report.write_daily_reports",
                        lambda _report, _target: {name: tmp_path / name
                                                  for name in ("report.json", "report.csv", "report.html")})
    code = cli.main(["daily-export", "--account-archive", str(archive_path),
                     "--runner-journal", str(tmp_path / "runner.jsonl"),
                     "--session-id", "session-1", "--output-dir", str(tmp_path / "out")])
    output = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT["OK"]
    assert set(output["outputs"]) == {"report.json", "report.csv", "report.html"}
    assert output["physical_orders"] is False
