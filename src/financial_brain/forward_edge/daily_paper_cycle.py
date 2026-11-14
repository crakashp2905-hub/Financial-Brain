"""Resumable orchestration for registered paper-book close bundles and daily reports."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from financial_brain.forward_edge.book_performance_report import (
    BookPerformanceError,
    build_book_performance_report,
    write_book_performance_report,
)
from financial_brain.forward_edge.cli import EXIT, cmd_daily_finalize
from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.kernel.registered_paper_books import RegisteredPaperBooks


class DailyPaperCycleError(ValueError):
    """A daily manifest is incomplete or its evidence does not match the frozen book plan."""


def run_daily_paper_cycle(manifest_path: Path) -> dict:
    """Finalize all planned books and write a content-addressed daily performance report.

    The caller supplies close marks, session IDs, runner journals and prior session rows. The
    operation is retryable per book: EOD archives and bundles are immutable/idempotent, so a
    failure on a later book can be resumed by rerunning the same manifest and captured inputs.
    """
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DailyPaperCycleError("daily-cycle manifest is missing or invalid JSON") from exc
    if not isinstance(manifest, dict):
        raise DailyPaperCycleError("daily-cycle manifest must be an object")
    required = ("session_date", "captured_at", "registry", "plan_id", "account_root",
                "books", "report_output_root")
    missing = [name for name in required if name not in manifest]
    if missing:
        raise DailyPaperCycleError(f"daily-cycle manifest missing fields: {', '.join(missing)}")
    session_date = manifest["session_date"]
    try:
        canonical_date = date.fromisoformat(session_date).isoformat()
    except (TypeError, ValueError) as exc:
        raise DailyPaperCycleError("session_date must be a canonical ISO exchange date") from exc
    if not isinstance(session_date, str) or canonical_date != session_date:
        raise DailyPaperCycleError("session_date must be a canonical ISO exchange date")
    books = manifest["books"]
    history = manifest.get("history_sessions", [])
    if not isinstance(books, list) or not isinstance(history, list):
        raise DailyPaperCycleError("books and history_sessions must be arrays")
    registry = RegisteredPaperBooks(Path(manifest["registry"]))
    plan_id = manifest["plan_id"]
    plan = next((row for row in registry.plans if row.plan_id == plan_id), None)
    if plan is None:
        raise DailyPaperCycleError("requested frozen paper-book plan is not registered")
    expected = {allocation.strategy_id: allocation for allocation in plan.books}
    supplied = {}
    for row in books:
        if not isinstance(row, dict) or not isinstance(row.get("strategy_id"), str):
            raise DailyPaperCycleError("each book entry needs its registered strategy_id")
        strategy_id = row["strategy_id"]
        if strategy_id in supplied or strategy_id not in expected:
            raise DailyPaperCycleError("book entries must uniquely match the frozen plan strategies")
        supplied[strategy_id] = row
    if set(supplied) != set(expected):
        raise DailyPaperCycleError("daily-cycle must include every account in the frozen plan")

    report_sessions = list(history)
    finalized = []
    account_root = Path(manifest["account_root"])
    for strategy_id, allocation in sorted(expected.items()):
        row = supplied[strategy_id]
        needed = ("session_id", "runner_journal", "marks", "archive_dir", "bundle_root")
        absent = [key for key in needed if key not in row]
        if absent:
            raise DailyPaperCycleError(
                f"book {strategy_id} missing fields: {', '.join(absent)}")
        account_path = registry.account_path(
            plan_id=plan_id, account_id=allocation.account_id, root=account_root)
        runner_path = Path(row["runner_journal"])
        if not account_path.is_file() or not runner_path.is_file():
            raise DailyPaperCycleError(f"book {strategy_id} account or runner journal is missing")
        try:
            runner_rows = DurableJournal(runner_path).records()
        except (OSError, ValueError) as exc:
            raise DailyPaperCycleError(f"book {strategy_id} runner journal failed verification") from exc
        session_id = row["session_id"]
        session_rows = [item for item in runner_rows if item.get("session_id") == session_id]
        session_plan = next((item for item in session_rows if item.get("t") == "SESSION_PLAN"), None)
        terminal = next((item for item in reversed(session_rows)
                         if item.get("t") in {"SESSION_CLOSED", "SESSION_INCOMPLETE"}), None)
        plan_config = session_plan.get("config") if isinstance(session_plan, dict) else None
        if (not isinstance(session_id, str) or not session_id.strip()
                or not isinstance(plan_config, dict)
                or plan_config.get("exchange_date") != session_date
                or plan_config.get("authority_strategy_id") != strategy_id
                or plan_config.get("authority_account_id") != allocation.account_id
                or terminal is None):
            raise DailyPaperCycleError(
                f"book {strategy_id} lacks a matching dated terminal session bound to its registered account")

        result, code = cmd_daily_finalize(
            account_journal_path=str(account_path), session_date=session_date,
            captured_at=manifest["captured_at"], marks_path=str(row["marks"]),
            runner_journal_path=str(runner_path), session_id=session_id,
            archive_dir=str(row["archive_dir"]), bundle_root=str(row["bundle_root"]))
        if code != EXIT["OK"]:
            raise DailyPaperCycleError(
                f"book {strategy_id} finalization failed: {result.get('error', 'incomplete evidence')}")
        archive_path = Path(row["archive_dir"]) / session_date / f"{allocation.account_id}.json"
        try:
            archive = json.loads(archive_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DailyPaperCycleError(f"book {strategy_id} close archive is missing or invalid") from exc
        report_sessions.append({
            "book_id": strategy_id, "session_id": session_id,
            "session_date": session_date, "runner_records": runner_rows,
            "account_archive": archive,
        })
        finalized.append({"strategy_id": strategy_id, **result})

    try:
        report = build_book_performance_report(report_sessions)
        report_files = write_book_performance_report(report, Path(manifest["report_output_root"]))
    except (BookPerformanceError, OSError, ValueError) as exc:
        raise DailyPaperCycleError(f"daily per-book report could not be written: {exc}") from exc
    return {"session_date": session_date, "plan_id": plan_id, "finalized_books": finalized,
            "report": report_files, "physical_orders": False}

