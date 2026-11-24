"""Read-only paper order blotter that keeps unresolved orders outside fill totals (FE-0117)."""
from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path

from financial_brain.forward_edge.order_journal import ORDER_STATES, OrderJournal


@dataclass(frozen=True)
class PaperOrderRow:
    order_id: str
    state: str
    quantity: int
    filled_quantity: int | None
    remaining_quantity: int | None
    occurred_at: str
    price: str | None
    replay_sha256: str

    @property
    def unresolved(self) -> bool:
        return self.state in {"CREATED", "SUBMITTED", "ACCEPTED", "WORKING", "PARTIAL",
                              "CANCEL_PENDING", "TIMED_OUT", "UNKNOWN"}

    @property
    def confirmed_filled_order(self) -> bool:
        return self.state == "FILLED" and self.filled_quantity == self.quantity


@dataclass(frozen=True)
class PaperOrderBlotter:
    orders: tuple[PaperOrderRow, ...]
    unresolved_count: int
    confirmed_filled_order_count: int
    confirmed_filled_quantity: int


def build_paper_order_blotter(journal: OrderJournal) -> PaperOrderBlotter:
    """Summarize the latest event per order; UNKNOWN never contributes to fill totals."""
    rows = []
    for order_id in journal.order_ids():
        events = journal.replay(order_id)
        if not events:
            continue
        latest = events[-1]
        state = str(latest["state"])
        if state not in ORDER_STATES:
            raise ValueError(f"unknown persisted order state {state!r}")
        quantity = int(latest["quantity"])
        filled = int(latest["filled_quantity"])
        is_unknown = state in {"UNKNOWN", "TIMED_OUT"}
        visible_filled = None if is_unknown else filled
        remaining = None if is_unknown else quantity - filled
        rows.append(PaperOrderRow(order_id, state, quantity, visible_filled, remaining,
                                  str(latest["occurred_at"]), latest["price"],
                                  journal.replay_hash(order_id)))
    rows.sort(key=lambda row: (row.occurred_at, row.order_id), reverse=True)
    unresolved = sum(row.unresolved for row in rows)
    filled_rows = [row for row in rows if row.confirmed_filled_order]
    confirmed_quantity = sum(row.filled_quantity or 0 for row in rows
                             if row.state not in {"UNKNOWN", "TIMED_OUT"})
    return PaperOrderBlotter(tuple(rows), unresolved, len(filled_rows), confirmed_quantity)


def render_order_blotter(blotter: PaperOrderBlotter, output: Path) -> Path:
    """Write an escaped paper-only blotter; it reports fills without asserting closed trades or P&L."""
    rows = []
    for item in blotter.orders:
        state_class = " unknown" if item.state in {"UNKNOWN", "TIMED_OUT"} else ""
        values = (item.order_id, item.state, str(item.quantity),
                  "Unknown" if item.filled_quantity is None else str(item.filled_quantity),
                  "Unknown" if item.remaining_quantity is None else str(item.remaining_quantity),
                  item.occurred_at, item.price or "-", item.replay_sha256)
        rows.append(f"<tr class='{state_class.strip()}'>" + "".join(
            "<td>" + html.escape(value) + "</td>" for value in values) + "</tr>")
    if not rows:
        rows.append("<tr><td colspan='8'>No paper order events recorded</td></tr>")
    page = ("<!doctype html><html lang='en'><meta charset='utf-8'><title>Paper order blotter</title>"
            "<style>body{font:14px system-ui;margin:2rem;color:#202630}table{border-collapse:collapse;width:100%}"
            "td,th{border-bottom:1px solid #ddd;text-align:left;padding:.45rem}.unknown{background:#fff1d6}"
            "code{overflow-wrap:anywhere}</style><main><h1>Paper order blotter</h1>"
            f"<p>Unresolved orders: <b>{blotter.unresolved_count}</b> · Confirmed fully filled orders: "
            f"<b>{blotter.confirmed_filled_order_count}</b> · Filled quantity: "
            f"<b>{blotter.confirmed_filled_quantity}</b></p>"
            "<p>Filled orders are not necessarily closed trades. UNKNOWN and unfilled orders are excluded from "
            "confirmed fill totals.</p><div style='overflow:auto'><table><thead><tr>"
            "<th>Order</th><th>State</th><th>Order qty</th><th>Confirmed fill qty</th><th>Remaining</th>"
            "<th>Last event time</th><th>Last price</th><th>Replay SHA-256</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table></div></main></html>")
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(page, encoding="utf-8")
    return destination
