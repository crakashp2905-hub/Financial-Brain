"""Independent journal-only balance and NAV audit for the cash-equity paper book."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from financial_brain.forward_edge.durable import DurableJournal


class PaperAuditError(ValueError):
    """The paper account journal cannot be reconciled independently."""


def audit_paper_account_journal(path: Path) -> dict:
    """Recompute cash, inventory cost basis, realized P&L, fees and NAV from journal facts.

    This deliberately does not instantiate ``PaperAccount`` or trust its in-memory
    OMS/ledger projections. A hash-chain failure or accounting inconsistency raises.
    """
    records = DurableJournal(Path(path)).records()
    if not records or records[0].get("t") != "ACCOUNT_OPEN":
        raise PaperAuditError("journal must start with ACCOUNT_OPEN")
    opening = records[0]
    currency = opening.get("currency")
    if not isinstance(currency, str) or len(currency) != 3:
        raise PaperAuditError("account opening currency is invalid")
    try:
        cash = Decimal(str(opening["cash"]))
        if not cash.is_finite() or cash <= 0:
            raise ValueError
    except (KeyError, InvalidOperation, ValueError) as exc:
        raise PaperAuditError("account opening cash must be positive and finite") from exc
    initial_cash = cash
    positions: dict[str, tuple[int, Decimal]] = {}
    last_fill_date: dict[str, date] = {}
    realized = Decimal(0)
    fees = Decimal(0)
    marks: dict[str, tuple[Decimal, datetime]] = {}
    flow_since_snapshot = Decimal(0)
    flow_timestamps: list[datetime] = []
    fill_ids: set[str] = set()
    fee_ids: set[str] = set()
    mark_ids: set[str] = set()
    snapshot_ids: set[str] = set()
    snapshot_dates: set[str] = set()
    snapshots: list[dict] = []
    issues: list[str] = []

    def amount(row: dict, key: str, *, positive: bool = False) -> Decimal:
        try:
            value = Decimal(str(row[key]))
        except (KeyError, InvalidOperation, TypeError) as exc:
            raise PaperAuditError(f"{row.get('t')} has invalid {key}") from exc
        if not value.is_finite() or (positive and value <= 0):
            raise PaperAuditError(f"{row.get('t')} has invalid {key}")
        return value

    def nav_at(captured_at: datetime) -> Decimal:
        total = cash
        for symbol, (quantity, _) in positions.items():
            if quantity == 0:
                continue
            marked = marks.get(symbol)
            if marked is None or marked[1] > captured_at:
                raise PaperAuditError(f"NAV snapshot has no eligible mark for {symbol}")
            fill_day = last_fill_date.get(symbol)
            if fill_day is not None and marked[1].date() < fill_day:
                raise PaperAuditError(f"NAV snapshot uses a pre-fill mark for {symbol}")
            total += Decimal(quantity) * marked[0]
        return total

    for row in records[1:]:
        kind = row.get("t")
        if kind == "INTENT" or kind == "STATE":
            continue
        if kind == "FILL":
            fill_id = str(row.get("fill_id", ""))
            if not fill_id or fill_id in fill_ids:
                raise PaperAuditError("fill IDs must be nonempty and unique")
            fill_ids.add(fill_id)
            symbol, side = str(row.get("symbol", "")), str(row.get("side", "")).upper()
            qty = row.get("quantity")
            if not symbol or type(qty) is not int or qty <= 0 or side not in {"BUY", "SELL"}:
                raise PaperAuditError("fill identity, side, or quantity is invalid")
            price, fee = amount(row, "price", positive=True), amount(row, "fee_amount")
            if fee < 0:
                raise PaperAuditError("fill fee cannot be negative")
            old_qty, old_basis = positions.get(symbol, (0, Decimal(0)))
            notional = price * qty
            if row.get("filled_on"):
                try:
                    fill_day = date.fromisoformat(str(row["filled_on"]))
                except ValueError as exc:
                    raise PaperAuditError("fill date is invalid") from exc
                last_fill_date[symbol] = max(last_fill_date.get(symbol, fill_day), fill_day)
            if side == "BUY":
                cash -= notional + fee
                positions[symbol] = (old_qty + qty, old_basis + notional)
            else:
                if qty > old_qty:
                    raise PaperAuditError(f"sell fill exceeds independently computed inventory for {symbol}")
                basis = old_basis * Decimal(qty) / Decimal(old_qty)
                realized += notional - basis
                cash += notional - fee
                positions[symbol] = (old_qty - qty, old_basis - basis)
            fees += fee
        elif kind == "FEE":
            fee_id = str(row.get("fee_id", ""))
            fee = amount(row, "amount", positive=True)
            if not fee_id or fee_id in fee_ids:
                raise PaperAuditError("standalone fee IDs must be nonempty and unique")
            fee_ids.add(fee_id)
            cash -= fee
            fees += fee
        elif kind == "EXTERNAL_FLOW":
            flow = amount(row, "amount")
            if flow == 0:
                raise PaperAuditError("external cash flow cannot be zero")
            try:
                occurred = datetime.fromisoformat(str(row["occurred_at"]).replace("Z", "+00:00"))
            except (KeyError, ValueError) as exc:
                raise PaperAuditError("external cash flow timestamp is invalid") from exc
            if occurred.tzinfo is None or occurred.utcoffset() is None:
                raise PaperAuditError("external cash flow timestamp must be timezone-aware")
            cash += flow
            flow_since_snapshot += flow
            flow_timestamps.append(occurred.astimezone(timezone.utc))
        elif kind == "MARK":
            mark_id = str(row.get("mark_id", ""))
            symbol = str(row.get("symbol", ""))
            if not mark_id or mark_id in mark_ids or not symbol:
                raise PaperAuditError("mark identity must be unique and include a symbol")
            mark_ids.add(mark_id)
            try:
                observed = datetime.fromisoformat(str(row["observed_at"]).replace("Z", "+00:00"))
            except (KeyError, ValueError) as exc:
                raise PaperAuditError("mark observation time is invalid") from exc
            if observed.tzinfo is None or observed.utcoffset() is None:
                raise PaperAuditError("mark observation time must be timezone-aware")
            price = amount(row, "price", positive=True)
            prior = marks.get(symbol)
            if prior is None or observed >= prior[1]:
                marks[symbol] = (price, observed.astimezone(timezone.utc))
        elif kind == "NAV_SNAPSHOT":
            snapshot_id = str(row.get("snapshot_id", ""))
            snapshot_day = str(row.get("session_date", ""))
            if not snapshot_id or snapshot_id in snapshot_ids or snapshot_day in snapshot_dates:
                raise PaperAuditError("NAV snapshot identities and session dates must be unique")
            snapshot_ids.add(snapshot_id)
            snapshot_dates.add(snapshot_day)
            try:
                date.fromisoformat(snapshot_day)
                captured = datetime.fromisoformat(str(row["captured_at"]).replace("Z", "+00:00"))
            except (KeyError, ValueError) as exc:
                raise PaperAuditError("NAV snapshot date or timestamp is invalid") from exc
            if captured.tzinfo is None or captured.utcoffset() is None:
                raise PaperAuditError("NAV snapshot timestamp must be timezone-aware")
            recorded_flow = amount(row, "external_flow")
            recorded_nav = amount(row, "nav", positive=True)
            computed_nav = nav_at(captured)
            if recorded_flow != flow_since_snapshot:
                issues.append(f"{snapshot_day}: external cash flow does not reconcile")
            if any(flow_time > captured.astimezone(timezone.utc) for flow_time in flow_timestamps):
                issues.append(f"{snapshot_day}: NAV contains a future-dated external cash flow")
            if recorded_nav != computed_nav:
                issues.append(f"{snapshot_day}: stored NAV does not reconcile")
            snapshots.append({"session_date": snapshot_day, "recorded_nav": str(recorded_nav),
                              "computed_nav": str(computed_nav), "recorded_external_flow": str(recorded_flow),
                              "computed_external_flow": str(flow_since_snapshot),
                              "reconciled": recorded_nav == computed_nav and recorded_flow == flow_since_snapshot})
            flow_since_snapshot = Decimal(0)
            flow_timestamps.clear()
        else:
            raise PaperAuditError(f"unknown paper account journal event: {kind}")

    current_positions = {symbol: {"quantity": qty, "cost_basis": str(basis)}
                         for symbol, (qty, basis) in sorted(positions.items()) if qty}
    final_marks = [observed for _, observed in marks.values()]
    latest = max(final_marks) if final_marks else datetime.min.replace(tzinfo=timezone.utc)
    current_nav = nav_at(latest) if current_positions else cash
    if issues:
        raise PaperAuditError("; ".join(issues))
    return {"status": "RECONCILED", "currency": currency, "initial_cash": str(initial_cash),
            "cash": str(cash), "positions": current_positions, "realized_pnl_gross": str(realized),
            "fees": str(fees), "realized_pnl_net": str(realized - fees), "current_nav": str(current_nav),
            "nav_snapshots": snapshots, "journal_event_count": len(records), "source": "DURABLE_JOURNAL_ONLY"}
