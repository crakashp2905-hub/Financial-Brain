"""Resumable orchestration for registered paper-book close bundles and daily reports."""
from __future__ import annotations

import json
import hashlib
from datetime import date, datetime
from decimal import Decimal
import os
from pathlib import Path

from financial_brain.forward_edge.book_performance_report import (
    BookPerformanceError,
    build_book_performance_report,
    write_book_performance_report,
)
from financial_brain.forward_edge.cli import EXIT, cmd_daily_finalize
from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.eod_paper_archive import EodArchiveError, validate_close_evidence
from financial_brain.kernel.paper_account import FeeSchedule, PaperAccount
from financial_brain.kernel.registered_paper_books import RegisteredPaperBooks


class DailyPaperCycleError(ValueError):
    """A daily manifest is incomplete or its evidence does not match the frozen book plan."""


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def build_daily_paper_manifest(*, registry_path: Path, plan_id: str, account_root: Path,
                               runner_journals: list[Path], session_date: str,
                               captured_at: str, marks_map_path: Path, archive_dir: Path,
                               bundle_root: Path, report_output_root: Path,
                               output_path: Path, history_sessions_path: Path | None = None) -> dict:
    """Derive a complete per-book session manifest from registered plans and verified journals.

    Owner-supplied close marks and prior history are explicit inputs; session IDs and account
    bindings are taken only from hash-chain-verified runner journals.
    """
    try:
        if date.fromisoformat(session_date).isoformat() != session_date:
            raise ValueError
        capture = datetime.fromisoformat(captured_at)
        if capture.tzinfo is None or capture.utcoffset() is None:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise DailyPaperCycleError("session date and captured_at must be canonical and timezone-aware") from exc
    registry_path = Path(registry_path).resolve()
    registry = RegisteredPaperBooks(registry_path)
    plan = next((item for item in registry.plans if item.plan_id == plan_id), None)
    if plan is None:
        raise DailyPaperCycleError("requested frozen paper-book plan is not registered")
    marks_mapping = json.loads(Path(marks_map_path).read_text(encoding="utf-8"))
    expected = {allocation.strategy_id: allocation for allocation in plan.books}
    if not isinstance(marks_mapping, dict) or set(marks_mapping) != set(expected):
        raise DailyPaperCycleError("marks map must contain one explicit file path for every registered strategy")
    history: list[dict] = []
    if history_sessions_path is not None:
        history_value = json.loads(Path(history_sessions_path).read_text(encoding="utf-8"))
        supplied_history = history_value.get("sessions") if isinstance(history_value, dict) else history_value
        if (not isinstance(supplied_history, list)
                or any(not isinstance(item, dict) for item in supplied_history)):
            raise DailyPaperCycleError("history file must contain a sessions array or a JSON array")
        history = supplied_history

    matching: dict[str, list[dict]] = {strategy_id: [] for strategy_id in expected}
    for journal_path in runner_journals:
        path = Path(journal_path).resolve()
        records = DurableJournal(path).records()
        session_ids = {row.get("session_id") for row in records
                       if row.get("t") in {"SESSION_CLOSED", "SESSION_INCOMPLETE"}}
        terminal_ids = {session_id for session_id in session_ids if isinstance(session_id, str)}
        for record in records:
            if record.get("t") != "SESSION_PLAN":
                continue
            session_id = record.get("session_id")
            config = record.get("config")
            if not isinstance(config, dict) or config.get("exchange_date") != session_date:
                continue
            strategy_id = config.get("authority_strategy_id")
            if strategy_id not in expected:
                continue
            allocation = expected[strategy_id]
            if config.get("authority_account_id") != allocation.account_id:
                continue
            if session_id not in terminal_ids:
                raise DailyPaperCycleError(f"runner session {session_id} for {strategy_id} is not terminal")
            matching[strategy_id].append({"session_id": session_id, "runner_journal": str(path)})

    books = []
    for strategy_id in sorted(expected):
        sessions = matching[strategy_id]
        if len(sessions) != 1:
            raise DailyPaperCycleError(
                f"expected exactly one terminal session for {strategy_id} on {session_date}; found {len(sessions)}")
        marks_path = Path(marks_mapping[strategy_id]).resolve()
        if not marks_path.is_file():
            raise DailyPaperCycleError(f"owner-provided close marks are missing for {strategy_id}")
        books.append({
            "strategy_id": strategy_id, "session_id": sessions[0]["session_id"],
            "runner_journal": sessions[0]["runner_journal"], "marks": str(marks_path),
            "archive_dir": str(Path(archive_dir).resolve()),
            "bundle_root": str(Path(bundle_root).resolve()),
        })
    core = {
        "schema_version": "1.0", "session_date": session_date,
        "captured_at": capture.isoformat(), "registry": str(registry_path),
        "plan_id": plan_id, "account_root": str(Path(account_root).resolve()),
        "books": books, "history_sessions": history,
        "report_output_root": str(Path(report_output_root).resolve()),
    }
    manifest = {**core, "manifest_sha256": hashlib.sha256(_canonical(core)).hexdigest()}
    requested_target = Path(output_path)
    if requested_target.is_symlink():
        raise DailyPaperCycleError("daily manifest output must not be a symbolic link")
    target = requested_target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if target.exists():
        if target.is_symlink() or target.read_bytes() != payload:
            raise DailyPaperCycleError("daily manifest output is immutable and already contains different inputs")
    else:
        temporary = target.with_suffix(target.suffix + ".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    return {"manifest_path": str(target), "manifest_sha256": manifest["manifest_sha256"],
            "book_count": len(books), "physical_orders": False}


def _prepare_daily_paper_cycle(manifest_path: Path) -> tuple[dict, list[dict]]:
    """Validate every account and input before the cycle performs its first close mutation."""
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DailyPaperCycleError("daily-cycle manifest is missing or invalid JSON") from exc
    if not isinstance(manifest, dict):
        raise DailyPaperCycleError("daily-cycle manifest must be an object")
    supplied_hash = manifest.get("manifest_sha256")
    if supplied_hash is not None:
        unsigned = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
        if (not isinstance(supplied_hash, str)
                or hashlib.sha256(_canonical(unsigned)).hexdigest() != supplied_hash):
            raise DailyPaperCycleError("daily-cycle manifest hash does not match its contents")
    required = ("session_date", "captured_at", "registry", "plan_id", "account_root",
                "books", "report_output_root")
    missing = [name for name in required if name not in manifest]
    if missing:
        raise DailyPaperCycleError(f"daily-cycle manifest missing fields: {', '.join(missing)}")
    for key in ("captured_at", "registry", "plan_id", "account_root", "report_output_root"):
        if not isinstance(manifest[key], str) or not manifest[key].strip():
            raise DailyPaperCycleError(f"manifest {key} must be a non-empty string")
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
    try:
        captured_at = datetime.fromisoformat(manifest["captured_at"])
    except (TypeError, ValueError) as exc:
        raise DailyPaperCycleError("captured_at must be an aware ISO-8601 timestamp") from exc
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

    try:
        build_book_performance_report(history)
    except BookPerformanceError as exc:
        raise DailyPaperCycleError(f"history_sessions failed report validation: {exc}") from exc
    history_keys = {(item["book_id"], item["session_id"]) for item in history}
    prepared = []
    account_root = Path(manifest["account_root"])
    for strategy_id, allocation in sorted(expected.items()):
        row = supplied[strategy_id]
        needed = ("session_id", "runner_journal", "marks", "archive_dir", "bundle_root")
        absent = [key for key in needed if key not in row]
        if absent:
            raise DailyPaperCycleError(
                f"book {strategy_id} missing fields: {', '.join(absent)}")
        for key in needed:
            if not isinstance(row[key], str) or not row[key].strip():
                raise DailyPaperCycleError(f"book {strategy_id} field {key} must be a non-empty string")
        account_path = registry.account_path(
            plan_id=plan_id, account_id=allocation.account_id, root=account_root)
        runner_path = Path(row["runner_journal"])
        marks_path = Path(row["marks"])
        if not account_path.is_file() or not runner_path.is_file() or not marks_path.is_file():
            raise DailyPaperCycleError(f"book {strategy_id} account, runner, or marks file is missing")
        try:
            account_rows = DurableJournal(account_path).records()
            marks = json.loads(marks_path.read_text(encoding="utf-8"))
            if not account_rows or account_rows[0].get("t") != "ACCOUNT_OPEN":
                raise ValueError("account journal has no durable opening record")
            opening = account_rows[0]
            if opening.get("account") != allocation.account_id:
                raise ValueError("account journal ID differs from registered allocation")
            opening_cash = Decimal(str(opening["cash"]))
            if opening_cash != Decimal(allocation.capital_inr):
                raise ValueError("account opening cash differs from frozen allocated capital")
            account = PaperAccount(
                account_path, account_id=allocation.account_id, initial_cash=opening_cash,
                currency=str(opening["currency"]),
                fee_schedule=FeeSchedule.from_dict(opening.get("fee_schedule")))
            if not isinstance(marks, dict):
                raise ValueError("marks file must contain a symbol-keyed JSON object")
            validate_close_evidence(account=account, session_date=date.fromisoformat(session_date),
                                    captured_at=captured_at, marks=marks)
        except (OSError, ValueError, KeyError, TypeError, EodArchiveError) as exc:
            raise DailyPaperCycleError(f"book {strategy_id} account or close evidence failed preflight: {exc}") from exc
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

        if (strategy_id, session_id) in history_keys:
            raise DailyPaperCycleError("history_sessions already contains this book/session")
        prepared.append({"strategy_id": strategy_id, "allocation": allocation,
                         "row": row, "account_path": account_path,
                         "runner_path": runner_path, "runner_rows": runner_rows})

    return manifest, prepared


def preflight_daily_paper_cycle(manifest_path: Path) -> dict:
    """Run non-mutating validation of all registered books and daily-cycle inputs."""
    manifest, prepared = _prepare_daily_paper_cycle(manifest_path)
    return {"session_date": manifest["session_date"], "plan_id": manifest["plan_id"],
            "books": [{"strategy_id": item["strategy_id"],
                       "account_id": item["allocation"].account_id,
                       "session_id": item["row"]["session_id"],
                       "runner_journal_head": DurableJournal(item["runner_path"]).head}
                      for item in prepared], "ready": True, "physical_orders": False}


def run_daily_paper_cycle(manifest_path: Path) -> dict:
    """Finalize all planned books and write a content-addressed daily performance report.

    The caller supplies close marks, session IDs, runner journals and prior session rows. The
    operation is retryable per book: EOD archives and bundles are immutable/idempotent, so a
    failure on a later book can be resumed by rerunning the same manifest and captured inputs.
    All input validation runs before the first account close mutation.
    """
    manifest, prepared = _prepare_daily_paper_cycle(manifest_path)
    report_sessions = list(manifest.get("history_sessions", []))
    finalized = []
    for item in prepared:
        strategy_id = item["strategy_id"]
        allocation = item["allocation"]
        row = item["row"]
        runner_rows = item["runner_rows"]

        result, code = cmd_daily_finalize(
            account_journal_path=str(item["account_path"]), session_date=manifest["session_date"],
            captured_at=manifest["captured_at"], marks_path=str(row["marks"]),
            runner_journal_path=str(item["runner_path"]), session_id=row["session_id"],
            archive_dir=str(row["archive_dir"]), bundle_root=str(row["bundle_root"]))
        if code != EXIT["OK"]:
            raise DailyPaperCycleError(
                f"book {strategy_id} finalization failed: {result.get('error', 'incomplete evidence')}")
        archive_path = Path(row["archive_dir"]) / manifest["session_date"] / f"{allocation.account_id}.json"
        try:
            archive = json.loads(archive_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DailyPaperCycleError(f"book {strategy_id} close archive is missing or invalid") from exc
        report_sessions.append({
            "book_id": strategy_id, "session_id": row["session_id"],
            "session_date": manifest["session_date"], "runner_records": runner_rows,
            "account_archive": archive,
        })
        finalized.append({"strategy_id": strategy_id, **result})

    try:
        report = build_book_performance_report(report_sessions)
        report_files = write_book_performance_report(report, Path(manifest["report_output_root"]))
    except (BookPerformanceError, OSError, ValueError) as exc:
        raise DailyPaperCycleError(f"daily per-book report could not be written: {exc}") from exc
    return {"session_date": manifest["session_date"], "plan_id": manifest["plan_id"], "finalized_books": finalized,
            "report": report_files, "physical_orders": False}

