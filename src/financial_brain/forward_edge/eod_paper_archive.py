"""End-of-session paper-account close, reconciliation, and immutable archive."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from financial_brain.kernel.oms import State, TERMINAL
from financial_brain.kernel.paper_account import PaperAccount, PaperAccountError

IST = ZoneInfo("Asia/Kolkata")


class EodArchiveError(ValueError):
    """The paper account cannot be safely closed or its archive is inconsistent."""


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _aware(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise EodArchiveError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _order_closeout(account: PaperAccount) -> list[dict]:
    """Cancel known local paper orders; unresolved venue-like states block NAV close."""
    for row in account.orders:
        state = State(row["state"])
        client_id = row["client_id"]
        if state in TERMINAL:
            continue
        if state in {State.UNKNOWN, State.SUBMITTED}:
            raise EodArchiveError(f"order {client_id} is unresolved at close ({state.value})")
        if state is State.CREATED:
            account.move(client_id, State.REJECTED)
        elif state in {State.ACCEPTED, State.PARTIAL}:
            account.move(client_id, State.CANCEL_PENDING)
            account.move(client_id, State.CANCELED)
        elif state is State.CANCEL_PENDING:
            account.move(client_id, State.CANCELED)
        else:
            raise EodArchiveError(f"order {client_id} cannot be reconciled at close ({state.value})")
    remaining = [row["client_id"] for row in account.orders if State(row["state"]) not in TERMINAL]
    if remaining:
        raise EodArchiveError(f"working orders remain at close: {', '.join(remaining)}")
    return account.orders


def close_and_archive_paper_session(*, account: PaperAccount, session_date: date,
                                    captured_at: datetime, marks: dict[str, dict],
                                    archive_dir: Path) -> dict:
    """Reconcile/cancel orders, mark inventory, snapshot NAV, and atomically archive exact state.

    `marks` is keyed by held symbol; each value carries positive `price`, aware `observed_at`,
    `source_id`, and a SHA-256 source identity. No stale/missing mark is replaced by zero.
    """
    if type(session_date) is not date:
        raise EodArchiveError("exchange session date is required")
    capture = _aware(captured_at, "end-of-session capture clock")
    if capture.astimezone(IST).date() != session_date:
        raise EodArchiveError("capture clock must fall on the exchange session date")
    if not isinstance(marks, dict):
        raise EodArchiveError("mark evidence must be a symbol-keyed object")
    positions = account.positions
    held_symbols = {position.symbol for position in positions}
    if set(marks) != held_symbols:
        missing = sorted(held_symbols - set(marks))
        extra = sorted(set(marks) - held_symbols)
        raise EodArchiveError(f"mark coverage mismatch; missing={missing}, unexpected={extra}")
    normalized_marks = {}
    for symbol in sorted(held_symbols):
        row = marks[symbol]
        if not isinstance(row, dict):
            raise EodArchiveError(f"mark evidence for {symbol} must be an object")
        try:
            price = Decimal(str(row["price"]))
            observed = _aware(datetime.fromisoformat(str(row["observed_at"])), f"{symbol} mark clock")
        except (KeyError, ValueError, InvalidOperation) as exc:
            raise EodArchiveError(f"mark evidence for {symbol} is invalid") from exc
        if not price.is_finite() or price <= 0 or observed > capture:
            raise EodArchiveError(f"mark for {symbol} must be positive and available by the close capture")
        if observed.astimezone(IST).date() != session_date:
            raise EodArchiveError(f"mark for {symbol} is not from the requested exchange session")
        source_id, source_sha = row.get("source_id"), row.get("source_sha256")
        if (not isinstance(source_id, str) or not source_id.strip()
                or not isinstance(source_sha, str) or len(source_sha) != 64
                or any(char not in "0123456789abcdef" for char in source_sha)):
            raise EodArchiveError(f"mark for {symbol} needs source identity and SHA-256")
        mark_id = f"eod:{account.account_id}:{session_date.isoformat()}:{symbol}"
        normalized_marks[symbol] = {"price": str(price), "observed_at": observed.isoformat(),
                                    "source_id": source_id, "source_sha256": source_sha,
                                    "mark_id": mark_id}

    orders = _order_closeout(account)
    for symbol, row in normalized_marks.items():
        account.record_mark(symbol, mark_id=row["mark_id"], price=Decimal(row["price"]),
                             observed_at=datetime.fromisoformat(row["observed_at"]))
    try:
        snapshot = account.record_nav_snapshot(
            session_date, snapshot_id=f"eod:{account.account_id}:{session_date.isoformat()}",
            captured_at=capture,
        )
        return_reconciliation = account.reconcile_total_return()
        if not return_reconciliation.get("reconciled"):
            raise EodArchiveError("NAV return does not reconcile to account ledger")
        if (account.ledger.balance(f"cash:{account.account_id}", account.currency) != account.cash
                or not account.ledger.trial_balance_ok()):
            raise EodArchiveError("OMS cash and double-entry ledger do not reconcile")
        inventory = []
        for position in account.positions:
            mark = normalized_marks[position.symbol]
            inventory.append({
                "symbol": position.symbol, "quantity": position.quantity,
                "average_cost": str(position.average_cost), "mark_price": mark["price"],
                "market_value": str(Decimal(position.quantity) * Decimal(mark["price"])),
                "mark_source_id": mark["source_id"], "mark_source_sha256": mark["source_sha256"],
            })
        body = {
            "schema_version": "1.0", "account_id": account.account_id,
            "currency": account.currency, "session_date": session_date.isoformat(),
            "captured_at": capture.isoformat(), "snapshot": snapshot,
            "cash": str(account.cash), "available_cash": str(account.available_cash),
            "equity": str(account.equity), "realized_pnl": str(account.realized_pnl),
            "net_realized_pnl": str(account.net_realized_pnl), "fees": str(account.fees),
            "taxes": str(account.taxes), "orders": orders, "inventory": inventory,
            "marks": normalized_marks, "return_reconciliation": return_reconciliation,
            "account_journal_seq": account.journal.seq, "account_journal_head": account.journal.head,
            "fee_schedule_sha256": account.fee_schedule.sha256 if account.fee_schedule else None,
            "physical_orders": False,
        }
        body["inventory_sha256"] = hashlib.sha256(_canonical(inventory)).hexdigest()
        body["snapshot_sha256"] = hashlib.sha256(_canonical(body)).hexdigest()
        payload = _canonical(body) + b"\n"
        target = Path(archive_dir) / session_date.isoformat() / f"{account.account_id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() != payload:
                raise EodArchiveError("end-of-session archive is immutable and already has different content")
        else:
            temporary = target.with_suffix(target.suffix + f".{uuid4().hex}.tmp")
            with temporary.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        verified = verify_prior_paper_open(account=account, archive_path=target)
        return {**body, "archive_path": str(target), "archive_sha256": hashlib.sha256(payload).hexdigest(),
                "next_open_reconciliation": verified}
    except PaperAccountError as exc:
        raise EodArchiveError(f"paper-account end-of-session reconciliation failed: {exc}") from exc


def verify_prior_paper_open(*, account: PaperAccount, archive_path: Path) -> dict:
    """Verify a restarted account exactly matches the archived NAV, inventory, orders and marks."""
    try:
        raw = Path(archive_path).read_bytes()
        body = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise EodArchiveError("prior-session archive is missing or invalid") from exc
    supplied = body.get("snapshot_sha256")
    unsigned = {key: value for key, value in body.items() if key != "snapshot_sha256"}
    if not isinstance(supplied, str) or hashlib.sha256(_canonical(unsigned)).hexdigest() != supplied:
        raise EodArchiveError("prior-session archive hash does not match its contents")
    if body.get("account_id") != account.account_id or body.get("currency") != account.currency:
        raise EodArchiveError("prior-session archive belongs to a different account")
    snapshot = next((row for row in account.nav_snapshots
                     if row["session_date"] == body.get("session_date")), None)
    if snapshot != body.get("snapshot"):
        raise EodArchiveError("account NAV snapshot does not match the archived session")
    if account.ledger.balance(f"cash:{account.account_id}", account.currency) != account.cash:
        raise EodArchiveError("reopened account cash does not match its ledger")
    if any(State(row["state"]) not in TERMINAL for row in account.orders):
        raise EodArchiveError("reopened account contains unresolved prior-session orders")
    inventory = []
    archived_marks = body.get("marks", {})
    for position in account.positions:
        mark = archived_marks.get(position.symbol)
        if not mark:
            raise EodArchiveError("archived inventory is missing its close mark")
        inventory.append({
            "symbol": position.symbol, "quantity": position.quantity,
            "average_cost": str(position.average_cost), "mark_price": mark["price"],
            "market_value": str(Decimal(position.quantity) * Decimal(mark["price"])),
            "mark_source_id": mark["source_id"], "mark_source_sha256": mark["source_sha256"],
        })
    if inventory != body.get("inventory") or hashlib.sha256(_canonical(inventory)).hexdigest() != body.get("inventory_sha256"):
        raise EodArchiveError("reopened inventory does not match the archived quantity and cost basis")
    if account.cash != Decimal(body["cash"]) or account.equity != Decimal(body["equity"]):
        raise EodArchiveError("reopened cash or NAV does not match the prior close")
    held_symbols = {row["symbol"] for row in body.get("inventory", [])}
    current_marks = [row for row in account.marks if row["symbol"] in held_symbols]
    expected_marks = [{"symbol": symbol, "price": row["price"], "observed_at": row["observed_at"],
                       "mark_id": row["mark_id"]} for symbol, row in sorted(archived_marks.items())]
    if current_marks != expected_marks:
        raise EodArchiveError("reopened mark set does not match the archived close")
    return {"verified": True, "account_id": account.account_id,
            "prior_session_date": body["session_date"], "prior_nav": body["equity"],
            "inventory_sha256": body["inventory_sha256"], "snapshot_sha256": supplied}
