from datetime import datetime, timedelta, timezone

from financial_brain.forward_edge.order_blotter import (
    build_paper_order_blotter,
    render_order_blotter,
)
from financial_brain.forward_edge.order_journal import OrderEvent, OrderJournal


NOW = datetime(2026, 11, 16, 4, tzinfo=timezone.utc)


def _append(journal, order_id, sequence, state, quantity, filled):
    journal.append(OrderEvent(order_id, f"{order_id}-{sequence}", sequence, state,
                              NOW + timedelta(seconds=sequence), quantity, filled,
                              "100.00" if filled else None, {}))


def test_blotter_keeps_unknown_and_unfilled_out_of_confirmed_fill_counts(tmp_path):
    journal = OrderJournal(tmp_path / "orders.sqlite3")
    _append(journal, "filled", 0, "CREATED", 5, 0)
    _append(journal, "filled", 1, "SUBMITTED", 5, 0)
    _append(journal, "filled", 2, "FILLED", 5, 5)
    _append(journal, "pending", 0, "CREATED", 8, 0)
    _append(journal, "pending", 1, "SUBMITTED", 8, 0)
    _append(journal, "partial", 0, "CREATED", 10, 0)
    _append(journal, "partial", 1, "SUBMITTED", 10, 0)
    _append(journal, "partial", 2, "PARTIAL", 10, 3)
    _append(journal, "unknown", 0, "CREATED", 4, 0)
    _append(journal, "unknown", 1, "SUBMITTED", 4, 0)
    _append(journal, "unknown", 2, "UNKNOWN", 4, 0)
    blotter = build_paper_order_blotter(journal)
    by_id = {row.order_id: row for row in blotter.orders}
    assert blotter.unresolved_count == 3
    assert blotter.confirmed_filled_order_count == 1
    assert blotter.confirmed_filled_quantity == 8
    assert by_id["unknown"].filled_quantity is None
    assert by_id["pending"].confirmed_filled_order is False
    assert by_id["partial"].confirmed_filled_order is False
    journal.close()


def test_blotter_page_marks_unknown_and_says_filled_is_not_closed_trade(tmp_path):
    journal = OrderJournal(tmp_path / "orders.sqlite3")
    _append(journal, "<unknown>", 0, "CREATED", 1, 0)
    _append(journal, "<unknown>", 1, "SUBMITTED", 1, 0)
    _append(journal, "<unknown>", 2, "UNKNOWN", 1, 0)
    page = render_order_blotter(build_paper_order_blotter(journal),
                                tmp_path / "blotter.html").read_text(encoding="utf-8")
    assert "<unknown>" not in page
    assert "&lt;unknown&gt;" in page
    assert "Unknown" in page
    assert "not necessarily closed trades" in page
    journal.close()
