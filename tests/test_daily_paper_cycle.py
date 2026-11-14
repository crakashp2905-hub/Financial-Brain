import json
from pathlib import Path
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

import financial_brain.forward_edge.daily_paper_cycle as cycle
from financial_brain.forward_edge.cli import main
from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.daily_evidence_bundle import verify_daily_bundle
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
                        "result": {"status": "NO_TRADE"}})
        books.append({
            "strategy_id": allocation.strategy_id,
            "session_id": f"s-{allocation.strategy_id}",
            "runner_journal": str(runner_path), "marks": str(tmp_path / "marks.json"),
            "archive_dir": str(tmp_path / "archives"), "bundle_root": str(tmp_path / "bundles"),
        })
    marks = tmp_path / "marks.json"
    marks.write_text("{}", encoding="utf-8")

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


def test_daily_paper_cycle_requires_exact_frozen_plan_membership_before_close(tmp_path, monkeypatch):
    manifest, manifest_path, _ = _fixture(tmp_path, monkeypatch)
    manifest["books"].pop()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(cycle.DailyPaperCycleError, match="every account in the frozen plan"):
        # Membership is validated before any account finalizer can mutate a journal.
        cycle.run_daily_paper_cycle(manifest_path)


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

