import json
from pathlib import Path
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

import financial_brain.forward_edge.daily_paper_cycle as cycle
from financial_brain.forward_edge.cli import main
from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.daily_evidence_bundle import verify_daily_bundle
from financial_brain.forward_edge.market_data import NormalizedEvent
from financial_brain.kernel.oms import State
from financial_brain.kernel.paper_account import FeeSchedule, PaperAccount


def _fixture(tmp_path, monkeypatch, *, mock_finalize=True, mock_reports=True):
    plan = SimpleNamespace(
        plan_id="plan-v1",
        books=(SimpleNamespace(strategy_id="alpha", account_id="paper-alpha", capital_inr=Decimal("1000")),
               SimpleNamespace(strategy_id="beta", account_id="paper-beta", capital_inr=Decimal("1000"))),
    )

    class Registry:
        plans = (plan,)

        def __init__(self, _path):
            pass

        def account_path(self, *, plan_id, account_id, root):
            assert plan_id == plan.plan_id
            return root / f"{account_id}.jsonl"

    monkeypatch.setattr(cycle, "RegisteredPaperBooks", Registry)
    account_root = tmp_path / "accounts"
    books = []
    for allocation in plan.books:
        account = account_root / f"{allocation.account_id}.jsonl"
        account.parent.mkdir(parents=True, exist_ok=True)
        PaperAccount(account, account_id=allocation.account_id, initial_cash=allocation.capital_inr,
                     fee_schedule=FeeSchedule("test-fees", date(2026, 1, 1),
                                               Decimal("1"), Decimal("1")))
        runner_path = tmp_path / f"{allocation.strategy_id}.runner.jsonl"
        journal = DurableJournal(runner_path)
        journal.append({"t": "SESSION_PLAN", "session_id": f"s-{allocation.strategy_id}",
                       "config": {"exchange_date": "2026-11-16",
                                  "authority_strategy_id": allocation.strategy_id,
                                  "authority_account_id": allocation.account_id}})
        journal.append({"t": "SESSION_CLOSED", "session_id": f"s-{allocation.strategy_id}",
                        "result": {"status": "NO_TRADE",
                                   "closed_at_utc": "2026-11-16T10:00:00+00:00"}})
        books.append({
            "strategy_id": allocation.strategy_id,
            "session_id": f"s-{allocation.strategy_id}",
            "runner_journal": str(runner_path), "marks": str(tmp_path / f"marks-{allocation.strategy_id}.json"),
            "archive_dir": str(tmp_path / "archives"), "bundle_root": str(tmp_path / "bundles"),
        })
        Path(books[-1]["marks"]).write_text("{}", encoding="utf-8")

    def finalize(**kwargs):
        archive_root = tmp_path / "archives" / kwargs["session_date"]
        archive_root.mkdir(parents=True, exist_ok=True)
        allocation_id = Path(kwargs["account_journal_path"]).stem
        (archive_root / f"{allocation_id}.json").write_text(
            json.dumps({"account_id": allocation_id, "session_date": kwargs["session_date"]}),
            encoding="utf-8")
        return {"session_id": kwargs["session_id"], "bundle_dir": str(tmp_path / "bundles")}, 0

    if mock_finalize:
        monkeypatch.setattr(cycle, "cmd_daily_finalize", finalize)
    reports = {}
    def build_report(sessions):
        reports["sessions"] = sessions
        return {"books": []}

    if mock_reports:
        monkeypatch.setattr(cycle, "build_book_performance_report", build_report)
        monkeypatch.setattr(cycle, "write_book_performance_report",
                            lambda report, root: {"report_sha256": "a" * 64, "report_root": str(root)})
    manifest = {
        "session_date": "2026-11-16", "captured_at": "2026-11-16T10:00:00+05:30",
        "registry": str(tmp_path / "registry.jsonl"), "plan_id": plan.plan_id,
        "account_root": str(account_root), "books": books,
        "history_sessions": [], "report_output_root": str(tmp_path / "reports"),
    }
    manifest_path = tmp_path / "daily.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest, manifest_path, reports


def test_daily_paper_cycle_finalizes_every_registered_book_and_builds_report(tmp_path, monkeypatch, capsys):
    _manifest, manifest_path, reports = _fixture(tmp_path, monkeypatch)

    code = main(["daily-paper-cycle", "--manifest", str(manifest_path)])
    output = json.loads(capsys.readouterr().out)

    assert code == 0
    assert output["physical_orders"] is False
    assert {row["strategy_id"] for row in output["finalized_books"]} == {"alpha", "beta"}
    assert {row["book_id"] for row in reports["sessions"]} == {"alpha", "beta"}
    assert all(row["account_archive"]["session_date"] == "2026-11-16"
               for row in reports["sessions"])
    portfolio_report = output["portfolio_report"]
    assert portfolio_report["ledger_trial_balance_ok"] is True
    assert portfolio_report["cross_book_cash_netting"] is False
    assert portfolio_report["books"]["TOTAL"]["cash"] == "2000"
    assert Path(portfolio_report["path"]).is_file()

    retry_code = main(["daily-paper-cycle", "--manifest", str(manifest_path)])
    retry_output = json.loads(capsys.readouterr().out)
    assert retry_code == 0
    assert retry_output["portfolio_report"]["report_sha256"] == portfolio_report["report_sha256"]


def test_daily_paper_cycle_requires_exact_frozen_plan_membership_before_close(tmp_path, monkeypatch):
    manifest, manifest_path, _ = _fixture(tmp_path, monkeypatch)
    manifest["books"].pop()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(cycle.DailyPaperCycleError, match="every account in the frozen plan"):
        # Membership is validated before any account finalizer can mutate a journal.
        cycle.run_daily_paper_cycle(manifest_path)


def test_daily_paper_preflight_is_read_only_and_checks_all_books_before_close(tmp_path, monkeypatch, capsys):
    manifest, manifest_path, _ = _fixture(tmp_path, monkeypatch)
    account_paths = [tmp_path / "accounts" / "paper-alpha.jsonl",
                     tmp_path / "accounts" / "paper-beta.jsonl"]
    before = [DurableJournal(path).head for path in account_paths]

    code = main(["daily-paper-preflight", "--manifest", str(manifest_path)])
    ready = json.loads(capsys.readouterr().out)

    assert code == 0
    assert ready["ready"] is True
    assert {row["strategy_id"] for row in ready["books"]} == {"alpha", "beta"}
    assert [DurableJournal(path).head for path in account_paths] == before

    Path(manifest["books"][1]["marks"]).unlink()
    code = main(["daily-paper-preflight", "--manifest", str(manifest_path)])
    blocked = json.loads(capsys.readouterr().out)
    assert code == 3
    assert blocked["ready"] is False
    assert [DurableJournal(path).head for path in account_paths] == before


def test_daily_manifest_builder_derives_book_sessions_and_writes_immutable_hashed_manifest(
        tmp_path, monkeypatch, capsys):
    manifest, _source_path, _ = _fixture(tmp_path, monkeypatch)
    marks_map_path = tmp_path / "marks-map.json"
    marks_map_path.write_text(json.dumps({
        row["strategy_id"]: row["marks"] for row in manifest["books"]}), encoding="utf-8")
    output_path = tmp_path / "built-daily-manifest.json"
    args = [
        "daily-paper-manifest", "--registry", manifest["registry"],
        "--plan-id", manifest["plan_id"], "--account-root", manifest["account_root"],
        "--session-date", manifest["session_date"], "--captured-at", manifest["captured_at"],
        "--marks-map", str(marks_map_path), "--archive-dir", str(tmp_path / "archives"),
        "--bundle-root", str(tmp_path / "bundles"), "--report-output-root", manifest["report_output_root"],
        "--output", str(output_path),
    ]
    for row in manifest["books"]:
        args.extend(["--runner-journal", row["runner_journal"]])

    code = main(args)
    result = json.loads(capsys.readouterr().out)
    assert code == 0
    assert result["book_count"] == 2
    built = json.loads(output_path.read_text(encoding="utf-8"))
    assert built["manifest_sha256"] == result["manifest_sha256"]
    assert {row["session_id"] for row in built["books"]} == {"s-alpha", "s-beta"}
    assert cycle.preflight_daily_paper_cycle(output_path)["ready"] is True

    repeated_code = main(args)
    repeated = json.loads(capsys.readouterr().out)
    assert repeated_code == 0
    assert repeated["manifest_sha256"] == result["manifest_sha256"]
    built["plan_id"] = "edited"
    output_path.write_text(json.dumps(built), encoding="utf-8")
    with pytest.raises(cycle.DailyPaperCycleError, match="manifest hash"):
        cycle.preflight_daily_paper_cycle(output_path)


def test_configured_daily_runner_builds_from_verified_close_receipts_and_retries_immutably(
        tmp_path, monkeypatch, capsys):
    _manifest, _source_path, _ = _fixture(tmp_path, monkeypatch)
    registry_path = tmp_path / "registry.jsonl"
    registry_path.write_text("fixture registry", encoding="utf-8")
    runner_paths = [tmp_path / "alpha.runner.jsonl", tmp_path / "beta.runner.jsonl"]
    config = {
        "schema_version": 1, "registry": str(registry_path), "plan_id": "plan-v1",
        "account_root": str(tmp_path / "accounts"),
        "runner_journals": [str(path) for path in runner_paths],
        "archive_dir": str(tmp_path / "archives"), "bundle_root": str(tmp_path / "bundles"),
        "report_output_root": str(tmp_path / "reports"), "manifest_root": str(tmp_path / "manifests"),
        "max_mark_age_seconds": 30,
    }
    config_path = tmp_path / "daily-cycle-config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    calls = []

    def run_cycle(path):
        calls.append(Path(path))
        return {"manifest": str(path), "physical_orders": False}

    monkeypatch.setattr(cycle, "run_daily_paper_cycle", run_cycle)
    first_code = main(["daily-paper-run", "--config", str(config_path), "--session-date", "2026-11-16"])
    first = json.loads(capsys.readouterr().out)
    manifest_path = calls[-1]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    second_code = main(["daily-paper-run", "--config", str(config_path), "--session-date", "2026-11-16"])
    second = json.loads(capsys.readouterr().out)

    assert first_code == second_code == 0
    assert first == second
    assert calls == [manifest_path, manifest_path]
    assert manifest["captured_at"] == "2026-11-16T10:00:00+00:00"
    assert manifest["mark_policy"] == "KITE_LATEST_VALID_BID_MAX_AGE_S=30.0"
    assert len(manifest["books"]) == 2


def test_daily_manifest_builder_derives_fresh_close_bid_from_kite_tagged_quote(tmp_path, monkeypatch, capsys):
    manifest, _source_path, _ = _fixture(tmp_path, monkeypatch)
    beta_account_path = tmp_path / "accounts" / "paper-beta.jsonl"
    beta_account = PaperAccount(
        beta_account_path, account_id="paper-beta", initial_cash=Decimal("1000"),
        fee_schedule=FeeSchedule("test-fees", date(2026, 1, 1), Decimal("1"), Decimal("1")))
    beta_account.submit_intent(client_id="buy-beta", symbol="NSE:INFY", side="BUY", quantity=1,
                               limit=Decimal("100"), reserve=Decimal("102"))
    beta_account.move("buy-beta", State.SUBMITTED)
    beta_account.move("buy-beta", State.ACCEPTED)
    beta_account.record_fill("buy-beta", fill_id="fill-beta", quantity=1, price=Decimal("100"),
                             filled_on=date(2026, 11, 16))

    event = NormalizedEvent(
        "QUOTE", "NSE:INFY", datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 16, 10, 3, tzinfo=timezone.utc),
        datetime(2026, 11, 16, 10, 4, tzinfo=timezone.utc),
        tuple(sorted({"source": "kite_connect", "exchange": "NSE", "raw_sha256": "a" * 64,
                      "exchange_timestamp_age_ms": "1000", "bid_price": "101", "ask_price": "101.10",
                      "bid_qty": "5", "ask_qty": "7"}.items())),
    )
    runner_path = tmp_path / "beta-kite.runner.jsonl"
    runner = DurableJournal(runner_path)
    runner.append({"t": "SESSION_PLAN", "session_id": "s-beta",
                   "config": {"exchange_date": "2026-11-16", "authority_strategy_id": "beta",
                              "authority_account_id": "paper-beta"}})
    runner.append({"t": "MARKET_EVENT", "session_id": "s-beta", "event_sha256": event.content_sha256(),
                   "event": {"kind": event.kind, "instrument": event.instrument,
                             "event_utc": event.event_utc.isoformat(),
                             "received_utc": event.received_utc.isoformat(),
                             "available_utc": event.available_utc.isoformat(),
                             "payload": [list(item) for item in event.payload]}})
    runner.append({"t": "SESSION_CLOSED", "session_id": "s-beta", "result": {"status": "NO_TRADE"}})
    manifest["books"][1]["runner_journal"] = str(runner_path)
    output_path = tmp_path / "derived-kite-manifest.json"
    args = [
        "daily-paper-manifest", "--registry", manifest["registry"],
        "--plan-id", manifest["plan_id"], "--account-root", manifest["account_root"],
        "--session-date", manifest["session_date"], "--captured-at", "2026-11-16T10:05:00+00:00",
        "--derive-kite-marks", "--max-mark-age-seconds", "300",
        "--archive-dir", str(tmp_path / "archives"), "--bundle-root", str(tmp_path / "bundles"),
        "--report-output-root", manifest["report_output_root"], "--output", str(output_path),
    ]
    for row in manifest["books"]:
        args.extend(["--runner-journal", row["runner_journal"]])

    code = main(args)
    result = json.loads(capsys.readouterr().out)
    assert code == 0
    assert result["book_count"] == 2
    built = json.loads(output_path.read_text(encoding="utf-8"))
    beta = next(row for row in built["books"] if row["strategy_id"] == "beta")
    derived_marks = json.loads(Path(beta["marks"]).read_text(encoding="utf-8"))
    assert derived_marks["NSE:INFY"]["price"] == "101"
    assert derived_marks["NSE:INFY"]["source_sha256"] == "a" * 64
    assert "KITE_LATEST_VALID_BID_MAX_AGE_S=300.0" == built["mark_policy"]
    assert cycle.preflight_daily_paper_cycle(output_path)["ready"] is True


def test_daily_paper_cycle_real_close_bundle_and_report_integration(tmp_path, monkeypatch, capsys):
    manifest, manifest_path, _ = _fixture(
        tmp_path, monkeypatch, mock_finalize=False, mock_reports=False)

    code = main(["daily-paper-cycle", "--manifest", str(manifest_path)])
    output = json.loads(capsys.readouterr().out)

    assert code == 0
    assert len(output["finalized_books"]) == 2
    assert len(output["report"]["files"]) == 2
    for row in manifest["books"]:
        bundle = Path(row["bundle_root"]) / manifest["session_date"] / (
            "paper-" + row["strategy_id"])
        assert verify_daily_bundle(bundle)["verified"] is True
    report = json.loads(Path(output["report"]["files"]["report.json"]).read_text(encoding="utf-8"))
    assert len(report["books"]) == 2
    assert all(book["session_status_counts"]["NO_TRADE"] == 1 for book in report["books"])
    assert all(book["account_archive_count"] == 1 for book in report["books"])

