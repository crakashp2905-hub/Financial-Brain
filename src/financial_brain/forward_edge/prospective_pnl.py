"""Session-by-session shadow versus filled-paper P&L ledger (PA-07-17)."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any


class ProspectivePnlError(ValueError):
    """Prospective P&L inputs are incomplete, inconsistent, or conflicting."""


def _finite_map(name: str, value: dict[str, Any], *, positive: bool = False) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not key.strip() or isinstance(raw, bool):
            raise ProspectivePnlError(f"{name} must contain finite numeric values")
        try:
            number = float(raw)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ProspectivePnlError(f"{name} must contain finite numeric values") from exc
        if not math.isfinite(number) or (positive and number <= 0):
            raise ProspectivePnlError(f"{name} must contain finite numeric values")
        out[key] = number
    return out


def build_session_pnl(*, strategy_id: str, account_id: str, session: date,
                      previous_accounting: dict[str, Any], current_accounting: dict[str, Any],
                      previous_marks: dict[str, float], current_marks: dict[str, float],
                      prospective_fill_receipts: list[dict[str, Any]],
                      shadow_weights: dict[str, float], shadow_starting_equity_inr: float,
                      shadow_costs_inr: float | None, shadow_cost_evidence_sha256: str | None,
                      marks_source_sha256: str) -> dict[str, Any]:
    """Reconcile session P&L to the canonical OMS paper-accounting snapshots.

    The active inventory must be exactly explainable by prospectively classified, filled
    receipts. Shadow performance is computed from frozen prior weights and marks, kept
    separate from actual filled-paper net P&L, and uses explicit caller-supplied estimated
    rebalance costs. No target-only signal contributes to the paper return.
    """
    if not strategy_id.strip() or not account_id.strip():
        raise ProspectivePnlError("strategy_id and account_id are required")
    if not isinstance(session, date):
        raise ProspectivePnlError("session must be a date")
    for label, digest in (("marks_source_sha256", marks_source_sha256),):
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ProspectivePnlError(f"{label} must be lowercase SHA-256")

    for label, accounting in (("previous", previous_accounting), ("current", current_accounting)):
        if accounting.get("account_id") != account_id:
            raise ProspectivePnlError(f"{label} accounting account does not match")
        if accounting.get("ledger_balanced") is not True or accounting.get("cash_reconciled") is not True:
            raise ProspectivePnlError(f"{label} accounting must be balanced and cash-reconciled")
        if label == "previous" and not accounting.get("net_pnl_eligible"):
            if accounting.get("positions") or float(accounting.get("charges_inr", 0)) != 0:
                raise ProspectivePnlError("previous open inventory must have costed net accounting")
    current_receipts_are_eligible = current_accounting.get("net_pnl_eligible") is True
    if current_receipts_are_eligible and current_accounting.get("status") != "RECONCILED_NET":
        raise ProspectivePnlError("current paper accounting must be RECONCILED_NET")
    if not current_receipts_are_eligible and (
            current_accounting.get("positions") or float(current_accounting.get("charges_inr", 0)) != 0):
        raise ProspectivePnlError("non-net-eligible current accounting must be a no-position cash book")
    if not current_receipts_are_eligible and current_accounting.get("status") != "RECONCILED_GROSS":
        raise ProspectivePnlError("cash-only outcome must be reconciled by the paper ledger")

    fills_by_instrument: dict[str, int] = {}
    receipt_costs = 0.0
    normalized_receipts = []
    for index, row in enumerate(prospective_fill_receipts):
        instrument, side = row.get("instrument_id"), row.get("side")
        quantity = row.get("filled_quantity")
        filled_on = row.get("session_date")
        if not isinstance(instrument, str) or not instrument.strip() or side not in {"BUY", "SELL"}:
            raise ProspectivePnlError(f"fill receipt {index} has invalid instrument or side")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise ProspectivePnlError(f"fill receipt {index} must have positive actual filled quantity")
        if not isinstance(filled_on, date) or filled_on > session:
            raise ProspectivePnlError(f"fill receipt {index} is not dated on or before this session")
        if row.get("evidence_class") != "LOCAL_PROSPECTIVE" or row.get("pre_entry_sealed") is not True:
            raise ProspectivePnlError(f"fill receipt {index} is not a sealed prospective paper fill")
        receipt_id = row.get("receipt_id")
        if not isinstance(receipt_id, str) or not receipt_id.strip():
            raise ProspectivePnlError(f"fill receipt {index} has no immutable receipt identity")
        for hash_name in ("receipt_sha256", "pre_entry_manifest_sha256"):
            receipt_hash = row.get(hash_name)
            if not isinstance(receipt_hash, str) or len(receipt_hash) != 64 \
                    or any(c not in "0123456789abcdef" for c in receipt_hash):
                raise ProspectivePnlError(f"fill receipt {index} lacks valid {hash_name}")
        fee = row.get("cost_inr")
        if isinstance(fee, bool) or not isinstance(fee, (int, float)) or not math.isfinite(fee) or fee < 0:
            raise ProspectivePnlError(f"fill receipt {index} has invalid itemized costs")
        signed = quantity if side == "BUY" else -quantity
        fills_by_instrument[instrument] = fills_by_instrument.get(instrument, 0) + signed
        receipt_costs += float(fee)
        normalized_receipts.append({"receipt_id": receipt_id, "instrument_id": instrument,
                                    "side": side, "filled_quantity": quantity,
                                    "session_date": filled_on.isoformat(), "cost_inr": float(fee),
                                    "receipt_sha256": row["receipt_sha256"],
                                    "pre_entry_manifest_sha256": row["pre_entry_manifest_sha256"]})
    if len({row["receipt_id"] for row in normalized_receipts}) != len(normalized_receipts):
        raise ProspectivePnlError("filled receipt identities must be unique")
    actual_positions = {key: int(value) for key, value in current_accounting.get("positions", {}).items() if value}
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
           for value in current_accounting.get("positions", {}).values()):
        raise ProspectivePnlError("canonical paper positions must be non-negative whole shares")
    if {key: value for key, value in fills_by_instrument.items() if value} != actual_positions:
        raise ProspectivePnlError("prospective filled receipts do not reconcile to actual paper inventory")
    if not math.isclose(receipt_costs, float(current_accounting.get("charges_inr", -1)),
                        rel_tol=0.0, abs_tol=0.01):
        raise ProspectivePnlError("prospective fill cost receipts do not reconcile to paper accounting costs")

    prior_marks = _finite_map("previous_marks", previous_marks, positive=True)
    marks = _finite_map("current_marks", current_marks, positive=True)
    prior_positions = {key: int(value) for key, value in previous_accounting.get("positions", {}).items() if value}
    if set(prior_positions) - set(prior_marks) or set(actual_positions) - set(marks):
        raise ProspectivePnlError("every prior/current held instrument needs a session mark")
    prior_cost = _finite_map("previous average costs", previous_accounting.get("average_cost_inr", {}), positive=True)
    average_cost = _finite_map("current average costs", current_accounting.get("average_cost_inr", {}), positive=True)
    if set(prior_positions) - set(prior_cost) or set(actual_positions) - set(average_cost):
        raise ProspectivePnlError("every held instrument needs reconciled average cost")

    prior_unrealized = sum(qty * (prior_marks[symbol] - prior_cost[symbol])
                           for symbol, qty in prior_positions.items())
    current_unrealized = sum(qty * (marks[symbol] - average_cost[symbol])
                             for symbol, qty in actual_positions.items())
    realized_delta = float(current_accounting["realized_gross_pnl_inr"]) - \
        float(previous_accounting["realized_gross_pnl_inr"])
    cost_delta = float(current_accounting["charges_inr"]) - float(previous_accounting["charges_inr"])
    if cost_delta < -0.01:
        raise ProspectivePnlError("cumulative paper charges cannot decrease")
    unrealized_change = current_unrealized - prior_unrealized
    gross_pnl = realized_delta + unrealized_change
    net_pnl = gross_pnl - cost_delta
    current_equity = float(current_accounting["cash_inr"]) + sum(
        qty * marks[symbol] for symbol, qty in actual_positions.items())
    previous_equity = float(previous_accounting["cash_inr"]) + sum(
        qty * prior_marks[symbol] for symbol, qty in prior_positions.items())
    if not math.isclose(current_equity - previous_equity, net_pnl, rel_tol=0.0, abs_tol=0.02):
        raise ProspectivePnlError("calculated paper P&L does not reconcile to cash plus marked positions")

    weights = _finite_map("shadow_weights", shadow_weights)
    if any(weight < 0 for weight in weights.values()) or sum(weights.values()) > 1.0 + 1e-9:
        raise ProspectivePnlError("shadow long-only weights must be non-negative and sum to at most one")
    if isinstance(shadow_starting_equity_inr, bool) or not isinstance(shadow_starting_equity_inr, (int, float)) \
            or not math.isfinite(shadow_starting_equity_inr) or shadow_starting_equity_inr <= 0:
        raise ProspectivePnlError("shadow starting equity must be finite and positive")
    if set(weights) - (set(prior_marks) & set(marks)):
        raise ProspectivePnlError("shadow holdings require both prior and current marks")
    shadow_gross = sum(weight * shadow_starting_equity_inr *
                       (marks[symbol] / prior_marks[symbol] - 1.0)
                       for symbol, weight in weights.items())
    shadow_net = None
    if shadow_costs_inr is not None:
        if isinstance(shadow_costs_inr, bool) or not isinstance(shadow_costs_inr, (int, float)) \
                or not math.isfinite(shadow_costs_inr) or shadow_costs_inr < 0:
            raise ProspectivePnlError("shadow_costs_inr must be finite and non-negative")
        if not isinstance(shadow_cost_evidence_sha256, str) or len(shadow_cost_evidence_sha256) != 64 \
                or any(c not in "0123456789abcdef" for c in shadow_cost_evidence_sha256):
            raise ProspectivePnlError("shadow cost estimate must carry a source SHA-256")
        shadow_net = shadow_gross - float(shadow_costs_inr)
    shadow = {"basis": "frozen prior-session target weights; no position is claimed as filled",
              "gross_pnl_inr": shadow_gross, "estimated_costs_inr": shadow_costs_inr,
              "net_pnl_inr": shadow_net, "cost_evidence_sha256": shadow_cost_evidence_sha256}
    session_fill_count = sum(row["session_date"] == session.isoformat()
                             for row in normalized_receipts)
    paper = {"basis": "canonical OMS filled inventory and itemized cost receipts",
             "evidence_class": ("LOCAL_PROSPECTIVE" if actual_positions or normalized_receipts
                                else "CASH_NO_FILLED_POSITIONS"),
             "filled_receipt_count": len(normalized_receipts),
             "session_filled_receipt_count": session_fill_count,
             "position_pnl_eligible": bool(actual_positions),
             "realized_gross_pnl_delta_inr": realized_delta,
             "unrealized_pnl_change_inr": unrealized_change, "gross_pnl_inr": gross_pnl,
             "transaction_costs_inr": cost_delta, "net_pnl_inr": net_pnl,
             "cash_inr": float(current_accounting["cash_inr"]),
             "holdings_value_inr": current_equity - float(current_accounting["cash_inr"]),
             "equity_inr": current_equity, "positions": actual_positions}
    body = {"schema": "fb.prospective_session_pnl.v1", "strategy_id": strategy_id,
            "account_id": account_id, "session": session.isoformat(),
            "marks_source_sha256": marks_source_sha256, "paper": paper, "shadow": shadow,
            "shadow_minus_paper_net_pnl_inr": (shadow_net - net_pnl if shadow_net is not None else None),
            "receipt_manifest_sha256": hashlib.sha256(json.dumps(normalized_receipts, sort_keys=True,
                separators=(",", ":"), allow_nan=False).encode()).hexdigest()}
    body["sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                               allow_nan=False).encode()).hexdigest()
    return body


class ProspectivePnlLedger:
    """SQLite append-only session P&L ledger with idempotent replay protection."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._connection = sqlite3.connect(self.path)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("""CREATE TABLE IF NOT EXISTS prospective_session_pnl (
            strategy_id TEXT NOT NULL, session TEXT NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(strategy_id, session))""")
        self._connection.commit()

    def append(self, report: dict[str, Any]) -> bool:
        if report.get("schema") != "fb.prospective_session_pnl.v1":
            raise ProspectivePnlError("unsupported prospective session P&L schema")
        strategy, session, digest = report.get("strategy_id"), report.get("session"), report.get("sha256")
        if not all(isinstance(x, str) and x.strip() for x in (strategy, session, digest)):
            raise ProspectivePnlError("strategy, session and digest are required")
        unsigned = {key: value for key, value in report.items() if key != "sha256"}
        computed = hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()
        if computed != digest:
            raise ProspectivePnlError("prospective session P&L digest does not match its payload")
        encoded = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False)
        prior = self._connection.execute(
            "SELECT digest, payload FROM prospective_session_pnl WHERE strategy_id=? AND session=?",
            (strategy, session)).fetchone()
        if prior:
            if prior == (digest, encoded):
                return False
            raise ProspectivePnlError("prospective session P&L is immutable; conflicting replay")
        latest = self._connection.execute(
            "SELECT MAX(session) FROM prospective_session_pnl WHERE strategy_id=?", (strategy,)).fetchone()[0]
        if latest is not None and session <= latest:
            raise ProspectivePnlError("new prospective session P&L must follow the latest recorded session")
        self._connection.execute("INSERT INTO prospective_session_pnl VALUES (?,?,?,?)",
                                 (strategy, session, digest, encoded))
        self._connection.commit()
        return True

    def monthly_report(self, *, strategy_id: str, year: int, month: int) -> dict[str, Any]:
        if not 1 <= month <= 12:
            raise ProspectivePnlError("month must be between 1 and 12")
        prefix = f"{year:04d}-{month:02d}-"
        rows = self._connection.execute(
            "SELECT payload FROM prospective_session_pnl WHERE strategy_id=? AND session LIKE ? ORDER BY session",
            (strategy_id, prefix + "%")).fetchall()
        payloads = [json.loads(row[0]) for row in rows]
        for payload in payloads:
            unsigned = {key: value for key, value in payload.items() if key != "sha256"}
            computed = hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":"),
                                       allow_nan=False).encode()).hexdigest()
            if computed != payload.get("sha256"):
                raise ProspectivePnlError("stored prospective session P&L digest failed verification")
        paper = [row["paper"] for row in payloads]
        shadow = [row["shadow"] for row in payloads]
        complete_shadow = all(row["net_pnl_inr"] is not None for row in shadow)
        return {"strategy_id": strategy_id, "year": year, "month": month,
                "sessions": len(payloads), "evidence_class": "LOCAL_PROSPECTIVE",
                "paper_filled_sessions": sum(row["session_filled_receipt_count"] > 0 for row in paper),
                "paper_gross_pnl_inr": sum(row["gross_pnl_inr"] for row in paper),
                "paper_costs_inr": sum(row["transaction_costs_inr"] for row in paper),
                "paper_net_pnl_inr": sum(row["net_pnl_inr"] for row in paper),
                "shadow_gross_pnl_inr": sum(row["gross_pnl_inr"] for row in shadow),
                "shadow_costs_inr": sum(row["estimated_costs_inr"] or 0.0 for row in shadow)
                    if complete_shadow else None,
                "shadow_net_pnl_inr": sum(row["net_pnl_inr"] for row in shadow)
                    if complete_shadow else None,
                "source_session_hashes": [row["sha256"] for row in payloads]}

    def close(self) -> None:
        self._connection.close()


__all__ = ["ProspectivePnlError", "ProspectivePnlLedger", "build_session_pnl"]
