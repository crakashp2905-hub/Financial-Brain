"""Versioned daily JSON/CSV/HTML exports derived from one verified paper report object."""
from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path
from html import escape

from .durable import DurableJournal

SCHEMA_VERSION = "1.0"
EVENT_TYPES = ("MARKET_EVENT", "DECISION", "PAPER_INTENT", "PAPER_EXECUTION",
               "PAPER_ORDER_UPDATE", "PAPER_SKIP")
TERMINAL_ORDER_STATES = {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}


class DailyReportError(ValueError):
    """Daily paper report source is missing, inconsistent, or not reproducible."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _decimal(value: object, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DailyReportError(f"{label} is not a decimal") from exc
    if not result.is_finite():
        raise DailyReportError(f"{label} is not finite")
    return result


def build_daily_report(*, account_archive: dict, runner_journal_path: Path,
                       session_id: str) -> dict:
    """Verify source artifacts and construct the one versioned schema used by all formats."""
    if not isinstance(account_archive, dict) or not isinstance(session_id, str) or not session_id.strip():
        raise DailyReportError("account archive and session ID are required")
    archive_hash = account_archive.get("snapshot_sha256")
    unsigned = {key: value for key, value in account_archive.items() if key != "snapshot_sha256"}
    if not isinstance(archive_hash, str) or hashlib.sha256(_canonical(unsigned)).hexdigest() != archive_hash:
        raise DailyReportError("paper account archive hash is invalid")
    inventory, orders = account_archive.get("inventory"), account_archive.get("orders")
    if not isinstance(inventory, list) or not isinstance(orders, list):
        raise DailyReportError("paper account archive inventory and orders must be lists")
    cash = _decimal(account_archive.get("cash"), "cash")
    equity = _decimal(account_archive.get("equity"), "equity")
    open_value = sum((_decimal(position.get("market_value"), "position market value")
                      for position in inventory if isinstance(position, dict)), Decimal(0))
    if len(inventory) != sum(isinstance(position, dict) for position in inventory):
        raise DailyReportError("paper account inventory contains malformed rows")
    if equity != cash + open_value:
        raise DailyReportError("equity does not equal cash plus marked inventory")
    if not isinstance(account_archive.get("snapshot"), dict) or _decimal(
            account_archive["snapshot"].get("nav"), "canonical NAV") != equity:
        raise DailyReportError("canonical journal NAV does not match archived equity")

    try:
        records = DurableJournal(Path(runner_journal_path)).records()
    except Exception as exc:
        raise DailyReportError("runner journal is missing or corrupt") from exc
    session = [row for row in records if row.get("session_id") == session_id]
    plan = next((row for row in session if row.get("t") == "SESSION_PLAN"), None)
    if plan is None or plan.get("config", {}).get("exchange_date") != account_archive.get("session_date"):
        raise DailyReportError("runner session plan and paper archive date do not match")
    closed = next((row for row in reversed(session) if row.get("t") == "SESSION_CLOSED"), None)
    incomplete = next((row for row in reversed(session) if row.get("t") == "SESSION_INCOMPLETE"), None)
    outcome = (f"INCOMPLETE:{incomplete.get('reason', 'UNKNOWN')}" if incomplete else
               str(closed.get("result", {}).get("status", "CLOSED_STATUS_UNKNOWN")) if closed else
               "UNFINISHED_NO_TERMINAL_RECORD")
    event_counts = {kind: sum(row.get("t") == kind for row in session) for kind in EVENT_TYPES}
    filled_orders = sum(str(row.get("state", "")).upper() in {"FILLED", "PARTIAL"}
                        and int(row.get("filled", 0) or 0) > 0 for row in orders)
    working_orders = sum(str(row.get("state", "")).upper() not in TERMINAL_ORDER_STATES for row in orders)
    totals = {
        "cash_inr": str(cash), "available_cash_inr": str(_decimal(account_archive.get("available_cash"), "available cash")),
        "equity_inr": str(equity), "open_position_market_value_inr": str(open_value),
        "realized_pnl_inr": str(_decimal(account_archive.get("realized_pnl"), "realized P&L")),
        "net_realized_pnl_inr": str(_decimal(account_archive.get("net_realized_pnl"), "net realized P&L")),
        "fees_inr": str(_decimal(account_archive.get("fees"), "fees")),
        "taxes_inr": str(_decimal(account_archive.get("taxes"), "taxes")),
        "inventory_count": len(inventory), "order_count": len(orders),
        "filled_order_count": filled_orders, "working_order_count": working_orders,
        **{f"event_{kind.lower()}_count": count for kind, count in event_counts.items()},
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "session_date": account_archive["session_date"], "session_id": session_id,
        "outcome": outcome, "account_id": account_archive["account_id"],
        "currency": account_archive["currency"], "physical_orders": False,
        "totals": totals, "inventory": inventory, "orders": orders,
        "account_archive_sha256": archive_hash,
        "runner_journal_head": next((row.get("journal_head") for row in reversed(session)
                                      if row.get("t") == "SESSION_CLOSED"), None),
    }
    report["report_sha256"] = hashlib.sha256(_canonical(report)).hexdigest()
    return report


def validate_daily_report(report: dict) -> None:
    if not isinstance(report, dict) or report.get("schema_version") != SCHEMA_VERSION:
        raise DailyReportError("unsupported daily report schema")
    supplied = report.get("report_sha256")
    unsigned = {key: value for key, value in report.items() if key != "report_sha256"}
    if not isinstance(supplied, str) or hashlib.sha256(_canonical(unsigned)).hexdigest() != supplied:
        raise DailyReportError("daily report hash does not match its schema data")
    totals = report.get("totals")
    if not isinstance(totals, dict):
        raise DailyReportError("daily report totals are missing")


def render_daily_reports(report: dict) -> dict[str, bytes]:
    """Serialize one validated schema into byte-stable JSON, CSV, and HTML artifacts."""
    validate_daily_report(report)
    json_bytes = (json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    flat = {"schema_version": report["schema_version"], "session_date": report["session_date"],
            "session_id": report["session_id"], "outcome": report["outcome"],
            "account_id": report["account_id"], "currency": report["currency"],
            "physical_orders": str(report["physical_orders"]).lower(), **report["totals"],
            "account_archive_sha256": report["account_archive_sha256"],
            "report_sha256": report["report_sha256"]}
    csv_stream = io.StringIO(newline="")
    writer = csv.DictWriter(csv_stream, fieldnames=list(flat), lineterminator="\n", extrasaction="raise")
    writer.writeheader()
    writer.writerow(flat)
    csv_bytes = csv_stream.getvalue().encode("utf-8")
    metadata = ("schema_version", "session_date", "session_id", "outcome", "account_id", "currency", "physical_orders")
    summary_rows = "".join(f"<tr><th>{escape(key)}</th><td data-field='{escape(key)}'>{escape(str(report[key]))}</td></tr>"
                           for key in metadata)
    total_rows = "".join(f"<tr><th>{escape(key)}</th><td data-field='{escape(key)}'>{escape(str(value))}</td></tr>"
                         for key, value in report["totals"].items())
    inventory_rows = "".join("<tr>" + "".join(f"<td>{escape(str(position.get(key, 'UNKNOWN')))}</td>"
                                               for key in ("symbol", "quantity", "average_cost", "mark_price", "market_value"))
                              + "</tr>" for position in report["inventory"])
    order_rows = "".join("<tr>" + "".join(f"<td>{escape(str(order.get(key, 'UNKNOWN')))}</td>"
                                           for key in ("client_id", "symbol", "side", "state", "filled", "remaining"))
                          + "</tr>" for order in report["orders"])
    html = ("<!doctype html><html><head><meta charset='utf-8'><title>Paper session report</title></head><body>"
            "<h1>Paper session report</h1><p>Simulated paper account evidence only; no live execution or profitability claim.</p>"
            f"<table id='metadata'><tbody>{summary_rows}</tbody></table><table id='totals'><tbody>{total_rows}</tbody></table>"
            f"<h2>Inventory</h2><table><thead><tr><th>Symbol</th><th>Quantity</th><th>Average cost</th><th>Mark</th><th>Market value</th></tr></thead><tbody>{inventory_rows}</tbody></table>"
            f"<h2>Orders</h2><table><thead><tr><th>Order</th><th>Symbol</th><th>Side</th><th>State</th><th>Filled</th><th>Remaining</th></tr></thead><tbody>{order_rows}</tbody></table>"
            f"<p>Report SHA-256: {escape(report['report_sha256'])}</p></body></html>")
    return {"report.json": json_bytes, "report.csv": csv_bytes, "report.html": html.encode("utf-8")}


def write_daily_reports(report: dict, output_dir: Path) -> dict[str, Path]:
    """Write deterministic format siblings from one report object using atomic replacement."""
    artifacts = render_daily_reports(report)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    output = {}
    for name, payload in artifacts.items():
        path = target / name
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_bytes(payload)
        temporary.replace(path)
        output[name] = path
    return output
