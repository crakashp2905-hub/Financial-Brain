"""Source-specific migration adapters for the legacy intraday and swing paper books.

All source databases are opened read-only. The adapters use recorded trade IDs, prices,
quantities, dates and cost fields; they never synthesize an exit for an open legacy trade.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb

from financial_brain.portfolio.canonical_ledger import (
    AssetClass,
    CanonicalLedgerError,
    CanonicalPortfolioLedger,
    EventKind,
    PortfolioEvent,
)


class LegacyMigrationError(ValueError):
    """A source book row lacks facts required for a lossless mapping."""


_MARKET_TZ = ZoneInfo("Asia/Kolkata")


def _scalar(value: Any) -> object:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, float):
        return format(value, ".17g")
    return value


def _row_hash(row: dict[str, Any]) -> str:
    payload = json.dumps({key: _scalar(value) for key, value in sorted(row.items())},
                         sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _number(value: Any, field: str, *, allow_zero: bool = False) -> Decimal:
    if value is None:
        raise LegacyMigrationError(f"legacy row is missing {field}")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise LegacyMigrationError(f"legacy field {field} is not numeric") from exc
    if not result.is_finite() or (result < 0 if allow_zero else result <= 0):
        raise LegacyMigrationError(f"legacy field {field} is outside the accepted range")
    return result


def _timestamp(value: Any, *, date_close: bool = False) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time(15, 30)) if date_close else datetime.combine(value, time())
    elif isinstance(value, str):
        if date_close and len(value) == 10:
            try:
                parsed = datetime.combine(date.fromisoformat(value), time(15, 30))
            except ValueError as exc:
                raise LegacyMigrationError(f"invalid legacy date: {value!r}") from exc
        else:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise LegacyMigrationError(f"invalid legacy timestamp: {value!r}") from exc
    else:
        raise LegacyMigrationError("legacy row is missing a usable event timestamp")
    if parsed.tzinfo is None:
        # These sources are Indian exchange paper books. Their runner documents date-only fills at NSE close,
        # while the intraday runner stores exchange-local wall-clock timestamps.
        parsed = parsed.replace(tzinfo=_MARKET_TZ)
    return parsed.isoformat()


def _event(*, account: str, sleeve: str, source: str, event_id: str, row_hash: str,
           kind: EventKind, at: Any, evidence: str, asset: AssetClass | None = None,
           instrument: str | None = None, side: str | None = None, quantity: Decimal | None = None,
           price: Decimal | None = None, cash_delta: Decimal | None = None,
           metadata: dict[str, object] | None = None, date_close: bool = False) -> PortfolioEvent:
    return PortfolioEvent(
        account_id=account, sleeve_id=sleeve, source_system=source, source_event_id=event_id,
        source_sha256=row_hash, event_kind=kind, occurred_at=_timestamp(at, date_close=date_close),
        asset_class=asset, instrument_id=instrument, side=side, quantity=quantity, price=price,
        cash_delta=cash_delta, evidence_class=evidence, metadata=metadata or {},
    )


def intraday_row_events(row: dict[str, Any]) -> list[PortfolioEvent]:
    """Map one persisted intraday paper trade; a missing exit becomes unresolved, never an inferred exit."""
    trade_id = str(row.get("trade_id") or "").strip()
    symbol = str(row.get("symbol") or "").strip()
    mode = str(row.get("mode") or "").strip().lower()
    version = str(row.get("version") or "").strip()
    if not trade_id or not symbol or not version or mode not in {"live", "replay"}:
        raise LegacyMigrationError("intraday row needs trade_id, symbol, version and live/replay mode")
    qty = _number(row.get("qty"), "qty")
    entry = _number(row.get("entry_px"), "entry_px")
    side = str(row.get("side") or "").strip().lower()
    if side not in {"long", "short"}:
        raise LegacyMigrationError("intraday side must be long or short")
    entry_side, exit_side = (("BUY", "SELL") if side == "long" else ("SELL", "BUY"))
    source = "paper_live.duckdb:trades"
    source_hash = _row_hash(row)
    evidence = "LOCAL_PAPER" if mode == "live" else "HISTORICAL_REPLAY"
    account = f"intraday:{version}:{mode}"
    sleeve = str(row.get("strategy") or "unassigned")
    instrument = f"NSE:{symbol}"
    events = [_event(
        account=account, sleeve=sleeve, source=source, event_id=f"{trade_id}:entry", row_hash=source_hash,
        kind=EventKind.FILL, at=row.get("entry_ts"), evidence=evidence, asset=AssetClass.EQUITY,
        instrument=instrument, side=entry_side, quantity=qty, price=entry,
        metadata={"legacy_trade_id": trade_id, "trade_side": side, "fill_basis": "PERSISTED_PAPER_FILL"},
    )]
    exit_ts, exit_px = row.get("exit_ts"), row.get("exit_px")
    if exit_ts is None or exit_px is None:
        events.append(_event(
            account=account, sleeve=sleeve, source=source, event_id=f"{trade_id}:unresolved-exit",
            row_hash=source_hash, kind=EventKind.UNRESOLVED_POSITION, at=row.get("entry_ts"),
            evidence=evidence, asset=AssetClass.EQUITY, instrument=instrument, quantity=qty,
            metadata={"legacy_trade_id": trade_id, "trade_side": side,
                      "reason": "legacy intraday row has no recorded exit fill; exit is not fabricated"},
        ))
        return events
    exit_price = _number(exit_px, "exit_px")
    exit_at = _timestamp(exit_ts)
    events.append(_event(
        account=account, sleeve=sleeve, source=source, event_id=f"{trade_id}:exit", row_hash=source_hash,
        kind=EventKind.FILL, at=exit_at, evidence=evidence, asset=AssetClass.EQUITY,
        instrument=instrument, side=exit_side, quantity=qty, price=exit_price,
        metadata={"legacy_trade_id": trade_id, "fill_basis": "PERSISTED_PAPER_FILL"},
    ))
    costs = row.get("costs")
    if costs is not None:
        fee = _number(costs, "costs", allow_zero=True)
        events.append(_event(
            account=account, sleeve=sleeve, source=source, event_id=f"{trade_id}:costs", row_hash=source_hash,
            kind=EventKind.FEE, at=exit_at, evidence=evidence, cash_delta=-fee,
            metadata={"legacy_trade_id": trade_id, "cost_basis": "PERSISTED_MODELLED_ROUND_TRIP_CHARGES"},
        ))
    return events


def swing_row_events(row: dict[str, Any]) -> list[PortfolioEvent]:
    """Map one swing book row; retain stored closes and marks, and keep model costs explicitly estimated."""
    ticker = str(row.get("ticker") or "").strip()
    version = str(row.get("version") or "").strip()
    mode = str(row.get("mode") or "").strip().lower()
    if not ticker or not version or mode not in {"live", "replay", "replay-lastweek", "replay-week"}:
        raise LegacyMigrationError("swing row needs ticker, version and a known live/replay mode")
    shares = row.get("shares")
    signal_day = row.get("signal_day")
    source_id = f"{version}:{mode}:{signal_day}:{ticker}"
    source_hash = _row_hash(row)
    is_live = mode == "live"
    evidence = "LOCAL_PAPER" if is_live else "HISTORICAL_REPLAY"
    account = f"swing:{version}:{mode}"
    sleeve = str(row.get("type") or "swing")
    instrument = f"NSE:{ticker}"
    if row.get("entry_day") is None or row.get("entry_px") is None or shares is None or Decimal(str(shares)) <= 0:
        return [_event(
            account=account, sleeve=sleeve, source="paper_book.duckdb:positions", event_id=f"{source_id}:unresolved",
            row_hash=source_hash, kind=EventKind.UNRESOLVED_POSITION,
            at=row.get("signal_day"), date_close=True, evidence=evidence, asset=AssetClass.EQUITY,
            instrument=instrument, quantity=Decimal(str(shares or 0)),
            metadata={"reason": "legacy candidate has no recorded entry fill", "legacy_position_id": source_id},
        )]
    quantity, entry_price = _number(shares, "shares"), _number(row.get("entry_px"), "entry_px")
    events = [_event(
        account=account, sleeve=sleeve, source="paper_book.duckdb:positions", event_id=f"{source_id}:entry",
        row_hash=source_hash, kind=EventKind.FILL, at=row["entry_day"], date_close=True, evidence=evidence,
        asset=AssetClass.EQUITY, instrument=instrument, side="BUY", quantity=quantity, price=entry_price,
        metadata={"legacy_position_id": source_id, "fill_basis": "RECORDED_NEXT_SESSION_CLOSE"},
    )]
    if row.get("exit_day") is not None and row.get("exit_px") is not None:
        exit_price = _number(row["exit_px"], "exit_px")
        exit_at = _timestamp(row["exit_day"], date_close=True)
        events.append(_event(
            account=account, sleeve=sleeve, source="paper_book.duckdb:positions", event_id=f"{source_id}:exit",
            row_hash=source_hash, kind=EventKind.FILL, at=exit_at, evidence=evidence,
            asset=AssetClass.EQUITY, instrument=instrument, side="SELL", quantity=quantity, price=exit_price,
            metadata={"legacy_position_id": source_id, "fill_basis": "RECORDED_DAILY_CLOSE"},
        ))
        if row.get("cost") is not None:
            cost_rate = _number(row["cost"], "cost", allow_zero=True)
            events.append(_event(
                account=account, sleeve=sleeve, source="paper_book.duckdb:positions", event_id=f"{source_id}:cost",
                row_hash=source_hash, kind=EventKind.FEE, at=exit_at, evidence=evidence,
                cash_delta=-(quantity * entry_price * cost_rate),
                metadata={"legacy_position_id": source_id, "cost_basis": "MODEL_ESTIMATE_RATE",
                          "cost_rate": format(cost_rate, "f")},
            ))
    elif row.get("exit_day") is not None:
        events.append(_event(
            account=account, sleeve=sleeve, source="paper_book.duckdb:positions",
            event_id=f"{source_id}:unresolved-exit", row_hash=source_hash,
            kind=EventKind.UNRESOLVED_POSITION, at=row["exit_day"], date_close=True, evidence=evidence,
            asset=AssetClass.EQUITY, instrument=instrument, quantity=quantity,
            metadata={"reason": "legacy exit date exists without an exit price", "legacy_position_id": source_id},
        ))
    elif row.get("last_day") is not None and row.get("last_px") is not None:
        events.append(_event(
            account=account, sleeve=sleeve, source="paper_book.duckdb:positions", event_id=f"{source_id}:mark",
            row_hash=source_hash, kind=EventKind.MARK, at=row["last_day"], date_close=True, evidence=evidence,
            asset=AssetClass.EQUITY, instrument=instrument, price=_number(row["last_px"], "last_px"),
            metadata={"legacy_position_id": source_id, "mark_basis": "PERSISTED_LAST_CLOSE"},
        ))
    else:
        events.append(_event(
            account=account, sleeve=sleeve, source="paper_book.duckdb:positions", event_id=f"{source_id}:unresolved",
            row_hash=source_hash, kind=EventKind.UNRESOLVED_POSITION, at=row["entry_day"], date_close=True,
            evidence=evidence, asset=AssetClass.EQUITY, instrument=instrument, quantity=quantity,
            metadata={"reason": "open legacy position has no persisted mark", "legacy_position_id": source_id},
        ))
    return events


def option_outcome_row_events(row: dict[str, Any]) -> list[PortfolioEvent]:
    """Map a persisted options-forward outcome without turning a signal into an order.

    Only outcomes with a recorded quote-based entry become fills. A missing exit is
    retained as unresolved, using the exact entry contract and quantity in the result.
    """
    try:
        payload = json.loads(str(row["payload"]))
        result = json.loads(str(row["result"]))
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise LegacyMigrationError("options outcome needs valid persisted intent and result JSON") from exc
    if not isinstance(payload, dict) or not isinstance(result, dict):
        raise LegacyMigrationError("options intent and result payloads must be JSON objects")
    payload_sha = hashlib.sha256(str(row["payload"]).encode("utf-8")).hexdigest()
    result_sha = hashlib.sha256(str(row["result"]).encode("utf-8")).hexdigest()
    if (row.get("payload_sha256") != payload_sha or row.get("intent_sha256") != payload_sha
            or row.get("result_sha256") != result_sha):
        raise LegacyMigrationError("options intent/outcome content hashes do not reconcile")
    key_parts = (row.get("cohort"), row.get("rule_version"), row.get("signal_id"))
    if not all(isinstance(value, str) and value.strip() for value in key_parts):
        raise LegacyMigrationError("options outcome needs cohort, rule version and signal identity")
    cohort, version, signal_id = (str(value) for value in key_parts)
    if payload.get("cohort") != cohort or payload.get("rule_version") != version or payload.get("signal_id") != signal_id:
        raise LegacyMigrationError("options outcome identity does not match its recorded intent")
    status = result.get("status")
    if status not in {"CLOSED", "NO_EXIT_QUOTE"}:
        # No quote or failed cost/liquidity hurdle means no simulated position existed.
        return []
    contract = str(result.get("contract") or "").strip()
    quantity = _number(result.get("quantity"), "options quantity")
    entry_total = _number(result.get("entry_premium_inr"), "entry_premium_inr")
    if not contract:
        raise LegacyMigrationError("options outcome is missing the observed contract symbol")
    mode = str(result.get("mode") or "").lower()
    if mode not in {"local_paper", "replay", "historical_replay"}:
        raise LegacyMigrationError("options outcome has an unsupported evidence mode")
    evidence = "HISTORICAL_REPLAY" if mode in {"replay", "historical_replay"} else "LOCAL_PAPER"
    source_hash = _row_hash(row)
    source = "options_paper_forward.duckdb:outcomes"
    account, sleeve = f"options:{cohort}:{version}", version
    instrument = f"NFO:{contract}"
    entry_at = result.get("entry_at")
    if entry_at is None:
        raise LegacyMigrationError("options outcome is missing observed entry time")
    entry_price = entry_total / quantity
    outcome_key = f"{cohort}:{version}:{signal_id}"
    events = [_event(
        account=account, sleeve=sleeve, source=source, event_id=f"{outcome_key}:entry",
        row_hash=source_hash, kind=EventKind.FILL, at=entry_at, evidence=evidence,
        asset=AssetClass.OPTION, instrument=instrument, side="BUY", quantity=quantity,
        price=entry_price, metadata={"signal_id": signal_id, "token": result.get("token"),
                                      "expiry": str(result.get("expiry")),
                                      "fill_basis": "PERSISTED_TWO_SIDED_QUOTE_OUTCOME"},
    )]
    if status == "NO_EXIT_QUOTE":
        events.append(_event(
            account=account, sleeve=sleeve, source=source, event_id=f"{outcome_key}:unresolved-exit",
            row_hash=source_hash, kind=EventKind.UNRESOLVED_POSITION,
            at=result.get("planned_exit_at") or row.get("planned_exit_at") or row.get("evaluated_at"),
            evidence=evidence, asset=AssetClass.OPTION, instrument=instrument, quantity=quantity,
            metadata={"signal_id": signal_id, "reason": "recorded option entry has no executable exit quote"},
        ))
        return events
    exit_total = _number(result.get("exit_premium_inr"), "exit_premium_inr")
    exit_at = result.get("exit_at")
    if exit_at is None:
        raise LegacyMigrationError("closed options outcome is missing observed exit time")
    events.append(_event(
        account=account, sleeve=sleeve, source=source, event_id=f"{outcome_key}:exit",
        row_hash=source_hash, kind=EventKind.FILL, at=exit_at, evidence=evidence,
        asset=AssetClass.OPTION, instrument=instrument, side="SELL", quantity=quantity,
        price=exit_total / quantity,
        metadata={"signal_id": signal_id, "fill_basis": "PERSISTED_TWO_SIDED_QUOTE_OUTCOME"},
    ))
    charges = _number(result.get("charges_inr"), "charges_inr", allow_zero=True)
    events.append(_event(
        account=account, sleeve=sleeve, source=source, event_id=f"{outcome_key}:charges",
        row_hash=source_hash, kind=EventKind.FEE, at=exit_at, evidence=evidence,
        cash_delta=-charges,
        metadata={"signal_id": signal_id, "cost_basis": "PERSISTED_MODELLED_OPTION_CHARGES"},
    ))
    return events


def _read_rows(path: Path, table: str) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    con = duckdb.connect(str(path), read_only=True)
    try:
        tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        if table not in tables:
            return []
        columns = [row[0] for row in con.execute(f'DESCRIBE "{table}"').fetchall()]
        return [dict(zip(columns, values)) for values in con.execute(f'SELECT * FROM "{table}"').fetchall()]
    finally:
        con.close()


def _read_option_outcomes(path: Path) -> list[dict[str, Any]]:
    """Read option intents joined to recorded outcomes; a missing file is an absent source."""
    if not path.is_file():
        return []
    con = duckdb.connect(str(path), read_only=True)
    try:
        tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        if not {"intents", "outcomes"}.issubset(tables):
            return []
        rows = con.execute("""SELECT i.cohort, i.rule_version, i.signal_id, i.planned_exit_at,
                    i.payload, i.payload_sha256, o.evaluated_at, o.intent_sha256,
                    o.result, o.result_sha256
                FROM intents AS i JOIN outcomes AS o USING (cohort, rule_version, signal_id)
                ORDER BY i.cohort, i.rule_version, i.signal_id""").fetchall()
        columns = ["cohort", "rule_version", "signal_id", "planned_exit_at", "payload",
                   "payload_sha256", "evaluated_at", "intent_sha256", "result", "result_sha256"]
        return [dict(zip(columns, row)) for row in rows]
    finally:
        con.close()


def migrate_legacy_paper_books(data_dir: Path, ledger_path: Path) -> dict[str, object]:
    """Migrate stored intraday and swing paper rows into a separate canonical SQLite file.

    Reads only `paper_live.duckdb` and `paper_book.duckdb`, writes only `ledger_path`, and
    leaves original books unchanged. Options and conventional MF trade books are not present
    in these two sources and are reported as absent rather than inferred from market-data files.
    """
    ledger = CanonicalPortfolioLedger(ledger_path)
    sources = {
        "intraday": (data_dir / "paper_live.duckdb", "trades", intraday_row_events),
        "swing": (data_dir / "paper_book.duckdb", "positions", swing_row_events),
    }
    options_path = data_dir / "options_paper_forward.duckdb"
    report: dict[str, object] = {"ledger_path": str(ledger_path), "sources": {}, "accounts": {},
                                 "unavailable_desks": ["ETF-specific", "mutual_fund"] if options_path.is_file()
                                 else ["options", "ETF-specific", "mutual_fund"],
                                 "original_databases_modified": False}
    summaries: dict[str, dict[str, Any]] = {}
    for name, (path, table, convert) in sources.items():
        rows = _read_rows(path, table)
        events: list[PortfolioEvent] = []
        rejected: list[dict[str, str]] = []
        for index, row in enumerate(rows):
            try:
                events.extend(convert(row))
            except (LegacyMigrationError, CanonicalLedgerError, ValueError, TypeError) as exc:
                rejected.append({"source_row": str(index), "reason": str(exc)})
        result = ledger.import_events(events)
        summaries[name] = {
            "source_path": str(path), "table": table, "source_rows": len(rows),
            "events": result, "rejected_rows": len(rejected),
            "rejected_row_reasons": rejected,
        }
    option_rows = _read_option_outcomes(options_path)
    option_events: list[PortfolioEvent] = []
    option_rejected: list[dict[str, str]] = []
    for index, row in enumerate(option_rows):
        try:
            option_events.extend(option_outcome_row_events(row))
        except (LegacyMigrationError, CanonicalLedgerError, ValueError, TypeError) as exc:
            option_rejected.append({"source_row": str(index), "reason": str(exc)})
    option_import = ledger.import_events(option_events)
    summaries["options"] = {
        "source_path": str(options_path), "table": "intents JOIN outcomes",
        "source_rows": len(option_rows), "events": option_import,
        "rejected_rows": len(option_rejected), "rejected_row_reasons": option_rejected,
    }
    report["sources"] = summaries
    account_summaries = {}
    for account_id in ledger.account_ids():
        snapshot = ledger.snapshot(account_id)
        account_summaries[account_id] = {
            "event_count": len(ledger.events(account_id)),
            "open_positions": sum(1 for position in snapshot["positions"] if position["quantity"] != 0),
            "unresolved_records": len(snapshot["unresolved_positions"]),
            "projection_complete": snapshot["projection_complete"],
            "cash_scope": snapshot["cash_scope"],
            "pnl_promotion_gate": snapshot["pnl_promotion_gate"],
        }
    report["accounts"] = account_summaries
    blocked_accounts = [account for account in account_summaries.values()
                        if account["pnl_promotion_gate"]["status"] == "BLOCKED"]
    promotion_reasons = {reason for account in blocked_accounts
                         for reason in account["pnl_promotion_gate"]["blockers"]}
    if any(int(source["rejected_rows"]) > 0 for source in summaries.values()):
        promotion_reasons.add("SOURCE_ROWS_REJECTED")
    if not account_summaries:
        promotion_reasons.add("NO_IMPORTED_ACCOUNTS")
    report["pnl_promotion_gate"] = {
        "status": "BLOCKED" if promotion_reasons else "ELIGIBLE_FOR_REVIEW",
        "blocked_accounts": len(blocked_accounts),
        "reason_codes": sorted(promotion_reasons),
        "note": "ELIGIBLE_FOR_REVIEW is not a profitability claim or automatic strategy promotion.",
    }
    return report


def audit_legacy_migration(data_dir: Path, ledger_path: Path, *, captured_at_utc: str) -> dict[str, object]:
    """Re-derive source events read-only and verify their exact normalized payloads in the canonical store."""
    ledger = CanonicalPortfolioLedger(ledger_path)
    specs = {
        "intraday": (data_dir / "paper_live.duckdb", "trades", intraday_row_events),
        "swing": (data_dir / "paper_book.duckdb", "positions", swing_row_events),
    }
    source_report: dict[str, Any] = {}
    expected_total = verified_total = rejected_total = 0
    for name, (path, table, convert) in specs.items():
        rows = _read_rows(path, table)
        expected: list[PortfolioEvent] = []
        rejected = 0
        for row in rows:
            try:
                expected.extend(convert(row))
            except (LegacyMigrationError, CanonicalLedgerError, ValueError, TypeError):
                rejected += 1
        verification = ledger.verify_import(expected)
        expected_total += len(expected)
        verified_total += verification["verified"]
        rejected_total += rejected
        source_report[name] = {
            "file": path.name, "table": table, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()
            if path.is_file() else None,
            "source_rows": len(rows), "expected_events": len(expected), "verification": verification,
            "rejected_rows": rejected,
        }
    options_path = data_dir / "options_paper_forward.duckdb"
    option_rows = _read_option_outcomes(options_path)
    expected_options: list[PortfolioEvent] = []
    rejected_options = 0
    for row in option_rows:
        try:
            expected_options.extend(option_outcome_row_events(row))
        except (LegacyMigrationError, CanonicalLedgerError, ValueError, TypeError):
            rejected_options += 1
    verification = ledger.verify_import(expected_options)
    expected_total += len(expected_options)
    verified_total += verification["verified"]
    rejected_total += rejected_options
    source_report["options"] = {
        "file": options_path.name,
        "sha256": hashlib.sha256(options_path.read_bytes()).hexdigest() if options_path.is_file() else None,
        "source_rows": len(option_rows), "expected_events": len(expected_options),
        "verification": verification, "rejected_rows": rejected_options,
    }
    account_summaries = []
    open_positions = unresolved = incomplete_accounts = missing_opening_balances = 0
    for account_id in ledger.account_ids():
        snapshot = ledger.snapshot(account_id)
        open_count = sum(1 for position in snapshot["positions"] if position["quantity"] != 0)
        unresolved_count = len(snapshot["unresolved_positions"])
        open_positions += open_count
        unresolved += unresolved_count
        incomplete_accounts += int(not snapshot["projection_complete"])
        missing_opening_balances += int(not snapshot["opening_balance_supplied"])
        account_summaries.append({"account_id": account_id, "event_count": len(ledger.events(account_id)),
                                  "open_positions": open_count, "unresolved_records": unresolved_count,
                                  "projection_complete": snapshot["projection_complete"],
                                  "opening_balance_supplied": snapshot["opening_balance_supplied"],
                                  "pnl_promotion_gate": snapshot["pnl_promotion_gate"]})
    promotion_blocked = [row for row in account_summaries
                         if row["pnl_promotion_gate"]["status"] == "BLOCKED"]
    promotion_reasons = sorted({reason for row in promotion_blocked
                                for reason in row["pnl_promotion_gate"]["blockers"]})
    source_integrity_errors = {
        "missing_source_events": sum(value["verification"]["missing"] for value in source_report.values()),
        "mismatched_source_events": sum(value["verification"]["mismatched"] for value in source_report.values()),
        "rejected_source_rows": rejected_total,
        "unscanned_ledger_events": ledger.event_count() - verified_total,
    }
    if any(source_integrity_errors.values()):
        promotion_reasons.append("SOURCE_RECONCILIATION_FAILED")
    return {
        "schema": "fb.canonical-paper-ledger-migration-audit.v1",
        "captured_at_utc": captured_at_utc,
        "source_databases_read_only": True,
        "source_rows_contain_no_trade_level_values": True,
        "sources": source_report,
        "ledger": {
            "file": ledger_path.name, "total_events": ledger.event_count(), "source_expected_events": expected_total,
            "source_events_verified": verified_total, "rejected_source_rows": rejected_total,
            "ledger_events_without_scanned_sources": ledger.event_count() - verified_total,
            "missing_source_events": sum(value["verification"]["missing"] for value in source_report.values()),
            "mismatched_source_events": sum(value["verification"]["mismatched"] for value in source_report.values()),
            "accounts": account_summaries, "open_sleeve_positions_across_run_books": open_positions,
            "unresolved_records": unresolved, "incomplete_projection_accounts": incomplete_accounts,
            "accounts_without_opening_balance": missing_opening_balances,
            "source_integrity_errors": source_integrity_errors,
            "note": "Position counts include separate historical replay/challenger books; they are not unique live holdings.",
        },
        "pnl_promotion_gate": {
            "status": "BLOCKED" if promotion_reasons else "ELIGIBLE_FOR_REVIEW",
            "blocked_accounts": len(promotion_blocked),
            "reason_codes": sorted(set(promotion_reasons)),
            "note": "ELIGIBLE_FOR_REVIEW is not a profitability claim or automatic strategy promotion.",
        },
        "unavailable_desk_books_in_scanned_sources": ["options", "ETF-specific", "mutual_fund"],
        "not_a_broker_reconciliation_or_profitability_report": True,
    }


__all__ = ["LegacyMigrationError", "audit_legacy_migration", "intraday_row_events",
           "migrate_legacy_paper_books", "option_outcome_row_events", "swing_row_events"]
