"""Resumable orchestration for registered paper-book close bundles and daily reports."""
from __future__ import annotations

import json
import hashlib
from datetime import date, datetime
from decimal import Decimal
from math import isfinite
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from financial_brain.forward_edge.book_performance_report import (
    BookPerformanceError,
    build_book_performance_report,
    write_book_performance_report,
)
from financial_brain.forward_edge.cli import EXIT, cmd_daily_finalize
from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.eod_paper_archive import EodArchiveError, validate_close_evidence
from financial_brain.forward_edge.market_data import NormalizedEvent
from financial_brain.kernel.paper_account import FeeSchedule, PaperAccount
from financial_brain.kernel.registered_paper_books import RegisteredPaperBooks


class DailyPaperCycleError(ValueError):
    """A daily manifest is incomplete or its evidence does not match the frozen book plan."""


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _derive_kite_close_marks(*, account: PaperAccount, runner_rows: list[dict], session_id: str,
                             session_date: date, captured_at: datetime,
                             max_age_seconds: float) -> dict[str, dict]:
    """Derive conservative long liquidation marks from provenance-bearing Kite quote events."""
    if not isfinite(max_age_seconds) or max_age_seconds <= 0:
        raise DailyPaperCycleError("Kite mark max age must be a finite positive number of seconds")
    held = {position.symbol for position in account.positions}
    if not held:
        return {}
    candidates: dict[str, list[tuple[datetime, str, dict]]] = {symbol: [] for symbol in held}
    for row in runner_rows:
        if row.get("t") != "MARKET_EVENT" or row.get("session_id") != session_id:
            continue
        try:
            value = row["event"]
            event = NormalizedEvent(
                kind=value["kind"], instrument=value["instrument"],
                event_utc=datetime.fromisoformat(value["event_utc"]),
                received_utc=datetime.fromisoformat(value["received_utc"]),
                available_utc=datetime.fromisoformat(value["available_utc"]),
                payload=tuple((str(key), str(item)) for key, item in value["payload"]),
                ingested_utc=datetime.fromisoformat(value["ingested_utc"])
                if value.get("ingested_utc") else None,
            )
            event_hash = event.content_sha256()
            if event_hash != row.get("event_sha256"):
                continue
        except (KeyError, TypeError, ValueError):
            continue
        if event.kind != "QUOTE" or event.instrument not in held:
            continue
        if event.available_utc > captured_at or event.available_utc.astimezone(
                ZoneInfo("Asia/Kolkata")).date() != session_date:
            continue
        payload = dict(event.payload)
        raw_hash = payload.get("raw_sha256")
        if payload.get("source") != "kite_connect" or payload.get("exchange") != "NSE":
            continue
        if (not isinstance(raw_hash, str) or len(raw_hash) != 64
                or any(char not in "0123456789abcdef" for char in raw_hash)):
            continue
        try:
            bid, ask = Decimal(payload["bid_price"]), Decimal(payload["ask_price"])
            bid_qty, ask_qty = int(payload["bid_qty"]), int(payload["ask_qty"])
        except (KeyError, ValueError, TypeError, ArithmeticError):
            continue
        if (not bid.is_finite() or not ask.is_finite() or bid <= 0 or ask < bid
                or bid_qty <= 0 or ask_qty <= 0):
            continue
        try:
            source_age_seconds = float(payload["exchange_timestamp_age_ms"]) / 1000
        except (KeyError, TypeError, ValueError):
            continue
        if not isfinite(source_age_seconds) or source_age_seconds < 0:
            continue
        event_to_available = (event.available_utc - event.event_utc).total_seconds()
        available_to_capture = (captured_at.astimezone(event.available_utc.tzinfo)
                                - event.available_utc).total_seconds()
        if max(source_age_seconds, event_to_available, available_to_capture) > max_age_seconds:
            continue
        mark = {"price": str(bid), "observed_at": event.available_utc.isoformat(),
                "source_id": f"kite_connect:{event.instrument}:{event.event_utc.isoformat()}",
                "source_sha256": raw_hash}
        candidates[event.instrument].append((event.available_utc, event_hash, mark))

    marks = {}
    for symbol, rows in candidates.items():
        if not rows:
            raise DailyPaperCycleError(f"no valid Kite close quote found for held position {symbol}")
        observed_at, _digest, mark = max(rows, key=lambda row: (row[0], row[1]))
        marks[symbol] = mark
    return marks


def _write_immutable_json(path: Path, value: dict) -> None:
    requested = Path(path)
    if requested.is_symlink():
        raise DailyPaperCycleError("generated close-mark file must not be a symbolic link")
    target = requested.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if target.exists():
        if target.is_symlink() or target.read_bytes() != payload:
            raise DailyPaperCycleError("derived close-mark file exists with different content")
        return
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


def build_daily_paper_manifest(*, registry_path: Path, plan_id: str, account_root: Path,
                               runner_journals: list[Path], session_date: str,
                               captured_at: str, marks_map_path: Path | None, archive_dir: Path,
                               bundle_root: Path, report_output_root: Path,
                               output_path: Path, history_sessions_path: Path | None = None,
                               derive_kite_marks: bool = False,
                               max_mark_age_seconds: float | None = None) -> dict:
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
    expected = {allocation.strategy_id: allocation for allocation in plan.books}
    marks_mapping = None
    if derive_kite_marks:
        if marks_map_path is not None or max_mark_age_seconds is None:
            raise DailyPaperCycleError("derived Kite marks require max_mark_age_seconds and cannot use a marks map")
    else:
        if marks_map_path is None:
            raise DailyPaperCycleError("owner-provided marks map is required unless Kite mark derivation is enabled")
        marks_mapping = json.loads(Path(marks_map_path).read_text(encoding="utf-8"))
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
        if not path.is_file():
            raise DailyPaperCycleError(f"runner journal is missing: {path}")
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
            matching[strategy_id].append({"session_id": session_id, "runner_journal": str(path),
                                          "runner_rows": records})

    books = []
    for strategy_id in sorted(expected):
        sessions = matching[strategy_id]
        if len(sessions) != 1:
            raise DailyPaperCycleError(
                f"expected exactly one terminal session for {strategy_id} on {session_date}; found {len(sessions)}")
        marks_path: Path
        if derive_kite_marks:
            assert max_mark_age_seconds is not None
            account_path = registry.account_path(
                plan_id=plan_id, account_id=expected[strategy_id].account_id, root=Path(account_root))
            if not account_path.is_file():
                raise DailyPaperCycleError(f"registered account journal is missing: {strategy_id}")
            account_rows = DurableJournal(account_path).records()
            if not account_rows or account_rows[0].get("t") != "ACCOUNT_OPEN":
                raise DailyPaperCycleError(f"registered account journal is missing its opening record: {strategy_id}")
            opening = account_rows[0]
            account = PaperAccount(
                account_path, account_id=expected[strategy_id].account_id,
                initial_cash=Decimal(str(opening["cash"])), currency=str(opening["currency"]),
                fee_schedule=FeeSchedule.from_dict(opening.get("fee_schedule")))
            marks = _derive_kite_close_marks(
                account=account, runner_rows=sessions[0]["runner_rows"],
                session_id=sessions[0]["session_id"], session_date=date.fromisoformat(session_date),
                captured_at=capture, max_age_seconds=float(max_mark_age_seconds))
            safe_id = hashlib.sha256(strategy_id.encode("utf-8")).hexdigest()[:24]
            marks_path = (Path(output_path).resolve().parent / "derived-close-marks"
                          / session_date / f"{safe_id}.json")
            _write_immutable_json(marks_path, marks)
        else:
            assert marks_mapping is not None
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
        "mark_policy": (f"KITE_LATEST_VALID_BID_MAX_AGE_S={max_mark_age_seconds}"
                        if derive_kite_marks else "OWNER_PROVIDED_CLOSE_MARKS"),
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


def run_configured_daily_paper_cycle(config_path: Path, session_date: str) -> dict:
    """Generate and run a daily close from operator-owned configuration and verified journals.

    Capture time comes from the hash-chain-verified SESSION_CLOSED receipts, making retries
    reuse the same immutable manifest and timestamp.
    """
    config_file = Path(config_path).resolve()
    try:
        config = json.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DailyPaperCycleError("daily-cycle config is unreadable JSON") from exc
    required = {"schema_version", "registry", "plan_id", "account_root", "runner_journals",
                "archive_dir", "bundle_root", "report_output_root", "manifest_root",
                "max_mark_age_seconds"}
    optional = {"history_sessions"}
    if (not isinstance(config, dict) or set(config) - required - optional
            or required - set(config) or type(config.get("schema_version")) is not int
            or config.get("schema_version") != 1):
        raise DailyPaperCycleError("daily-cycle config has an unsupported schema or fields")
    try:
        day = date.fromisoformat(session_date)
        if day.isoformat() != session_date:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise DailyPaperCycleError("session date must be a canonical YYYY-MM-DD value") from exc

    def resolve_path(key: str) -> Path:
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise DailyPaperCycleError(f"daily-cycle config path {key} is required")
        candidate = Path(value)
        return (config_file.parent / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()

    runner_values = config.get("runner_journals")
    if (not isinstance(runner_values, list) or not runner_values
            or any(not isinstance(value, str) or not value.strip() for value in runner_values)):
        raise DailyPaperCycleError("runner_journals must be a non-empty array of paths")
    runner_paths = []
    for value in runner_values:
        candidate = Path(value)
        runner_paths.append((config_file.parent / candidate).resolve()
                            if not candidate.is_absolute() else candidate.resolve())

    registry_path = resolve_path("registry")
    if not registry_path.is_file():
        raise DailyPaperCycleError("configured paper-book registry journal is missing")
    plan_id = config.get("plan_id")
    if not isinstance(plan_id, str) or not plan_id:
        raise DailyPaperCycleError("plan_id must be a non-empty string")
    registry = RegisteredPaperBooks(registry_path)
    plan = next((item for item in registry.plans if item.plan_id == plan_id), None)
    if plan is None:
        raise DailyPaperCycleError("configured frozen paper-book plan is not registered")
    expected = {allocation.strategy_id: allocation for allocation in plan.books}
    closes: dict[str, list[datetime]] = {strategy_id: [] for strategy_id in expected}
    for runner_path in runner_paths:
        if not runner_path.is_file():
            raise DailyPaperCycleError(f"runner journal is missing: {runner_path}")
        records = DurableJournal(runner_path).records()
        planned_sessions = {}
        for row in records:
            if row.get("t") != "SESSION_PLAN" or not isinstance(row.get("config"), dict):
                continue
            cfg = row["config"]
            strategy_id = cfg.get("authority_strategy_id")
            if not isinstance(strategy_id, str):
                continue
            allocation = expected.get(strategy_id)
            session_id = row.get("session_id")
            if (cfg.get("exchange_date") == session_date and allocation is not None
                    and cfg.get("authority_account_id") == allocation.account_id
                    and isinstance(session_id, str) and session_id):
                planned_sessions[session_id] = strategy_id
        for row in records:
            session_id = row.get("session_id")
            if not isinstance(session_id, str):
                continue
            strategy_id = planned_sessions.get(session_id)
            if row.get("t") != "SESSION_CLOSED" or strategy_id is None:
                continue
            result = row.get("result")
            timestamp = result.get("closed_at_utc") if isinstance(result, dict) else None
            try:
                if not isinstance(timestamp, str):
                    raise ValueError
                closed_at = datetime.fromisoformat(timestamp)
                if closed_at.tzinfo is None or closed_at.utcoffset() is None:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise DailyPaperCycleError(
                    f"closed runner session for {strategy_id} has no timezone-aware close receipt") from exc
            if closed_at.astimezone(ZoneInfo("Asia/Kolkata")).date() != day:
                raise DailyPaperCycleError(f"closed runner session for {strategy_id} is outside the exchange date")
            closes[strategy_id].append(closed_at)
    for strategy_id, timestamps in closes.items():
        if len(timestamps) != 1:
            raise DailyPaperCycleError(
                f"expected exactly one timestamped closed runner session for {strategy_id}; found {len(timestamps)}")
    captured_at = max(timestamp for timestamps in closes.values() for timestamp in timestamps)
    safe_plan_id = hashlib.sha256(plan_id.encode("utf-8")).hexdigest()[:24]
    manifest_path = resolve_path("manifest_root") / session_date / f"{safe_plan_id}.json"
    history_path = None
    history_value = config.get("history_sessions")
    if history_value is not None:
        if not isinstance(history_value, str) or not history_value.strip():
            raise DailyPaperCycleError("history_sessions must be a path when provided")
        history_candidate = Path(history_value)
        history_path = ((config_file.parent / history_candidate).resolve()
                        if not history_candidate.is_absolute() else history_candidate.resolve())
    try:
        if isinstance(config["max_mark_age_seconds"], bool):
            raise ValueError
        max_age = float(config["max_mark_age_seconds"])
        if not isfinite(max_age) or max_age <= 0:
            raise ValueError
    except (TypeError, ValueError, OverflowError) as exc:
        raise DailyPaperCycleError("max_mark_age_seconds must be finite and positive") from exc

    build_daily_paper_manifest(
        registry_path=registry_path, plan_id=plan_id, account_root=resolve_path("account_root"),
        runner_journals=runner_paths, session_date=session_date,
        captured_at=captured_at.isoformat(), marks_map_path=None, derive_kite_marks=True,
        max_mark_age_seconds=max_age, archive_dir=resolve_path("archive_dir"),
        bundle_root=resolve_path("bundle_root"), report_output_root=resolve_path("report_output_root"),
        output_path=manifest_path, history_sessions_path=history_path,
    )
    return run_daily_paper_cycle(manifest_path)


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

