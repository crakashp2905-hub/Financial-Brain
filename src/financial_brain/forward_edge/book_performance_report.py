"""By-book opportunity, execution, quote-coverage, and after-cost contribution reports."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from decimal import Decimal, InvalidOperation


class BookPerformanceError(ValueError):
    """Book evidence is inconsistent or lacks a reproducible denominator."""


def _canonical(value: dict | list) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _archive(account: object, session_date: str) -> dict:
    if account is None:
        return None
    if not isinstance(account, dict):
        raise BookPerformanceError("account archive must be a JSON object or missing")
    digest = account.get("snapshot_sha256")
    unsigned = {key: value for key, value in account.items() if key != "snapshot_sha256"}
    if (not isinstance(digest, str)
            or hashlib.sha256(_canonical(unsigned)).hexdigest() != digest):
        raise BookPerformanceError("account archive snapshot hash is invalid")
    if account.get("session_date") != session_date:
        raise BookPerformanceError("account archive date differs from the report session")
    reconciliation = account.get("return_reconciliation")
    required = ("economic_return_inr", "nav_return_inr", "realized_price_pnl_inr",
                "unrealized_price_pnl_inr", "gross_dividend_income_inr",
                "gross_cash_yield_income_inr", "fees_inr", "taxes_inr", "reconciled")
    if (not isinstance(reconciliation, dict) or any(key not in reconciliation for key in required)
            or reconciliation.get("reconciled") is not True):
        raise BookPerformanceError("account archive return reconciliation is incomplete")
    return account


def _decimal(value: object, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise BookPerformanceError(f"{field} must be a finite decimal") from exc
    if not result.is_finite():
        raise BookPerformanceError(f"{field} must be a finite decimal")
    return result


def _session_outcome(records: list[dict]) -> tuple[str, dict | None]:
    incomplete = next((row for row in reversed(records) if row.get("t") == "SESSION_INCOMPLETE"), None)
    if incomplete is not None:
        return "INCOMPLETE", incomplete
    closed = next((row for row in reversed(records) if row.get("t") == "SESSION_CLOSED"), None)
    if closed is None:
        return "UNRECORDED", None
    status = closed.get("result", {}).get("status")
    return ("NO_TRADE" if status in {"NO_TRADE", "REJECTED"} else "CLOSED"), closed


def build_book_performance_report(sessions: list[dict]) -> dict:
    """Aggregate all supplied sessions; missing outcomes and evidence stay in denominators.

    Each row has `book_id`, `session_id`, `session_date`, `runner_records`, and an optional
    `account_archive` from the same date. Runner records may be a shared journal; only the
    requested session ID is used. EOD account archives carry cumulative, flow-adjusted returns,
    which are differenced in date order to avoid summing cumulative P&L repeatedly.
    """
    if not isinstance(sessions, list):
        raise BookPerformanceError("session evidence must be a list")
    grouped: dict[str, list[dict]] = defaultdict(list)
    session_keys = set()
    for item in sessions:
        if not isinstance(item, dict):
            raise BookPerformanceError("each book session must be an object")
        book_id, session_id, session_date = item.get("book_id"), item.get("session_id"), item.get("session_date")
        if not all(isinstance(value, str) and value.strip() for value in (book_id, session_id, session_date)):
            raise BookPerformanceError("book id, session id and exchange date are required")
        key = (book_id, session_id)
        if key in session_keys:
            raise BookPerformanceError("book/session identity is duplicated")
        session_keys.add(key)
        try:
            from datetime import date
            if date.fromisoformat(session_date).isoformat() != session_date:
                raise ValueError
        except ValueError as exc:
            raise BookPerformanceError("session date must be canonical ISO date") from exc
        account = _archive(item.get("account_archive"), session_date)
        records = item.get("runner_records", [])
        if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
            raise BookPerformanceError("runner records must be a list of journal event objects")
        selected = [row for row in records if row.get("session_id") == session_id]
        grouped[book_id].append({**item, "account_archive": account, "runner_records": selected})

    books = []
    for book_id in sorted(grouped):
        rows = sorted(grouped[book_id], key=lambda row: (row["session_date"], row["session_id"]))
        account_ids = {row["account_archive"]["account_id"] for row in rows if row["account_archive"]}
        if len(account_ids) > 1:
            raise BookPerformanceError("one paper book cannot mix account identities")
        status_counts = {name: 0 for name in ("CLOSED", "NO_TRADE", "INCOMPLETE", "UNRECORDED")}
        candidate_count = 0
        candidate_count_known = True
        order_intent_count = 0
        execution_count = 0
        filled_intents = set()
        fill_ids = set()
        skip_count = 0
        coverage_sessions = 0
        coverage_missing_sessions = 0
        planned_instrument_sessions = 0
        quoted_instrument_sessions = 0
        candidate_quote_denominator = 0
        candidate_quote_covered = 0
        candidate_quote_sessions = 0
        p_and_l_archives = {}
        for row in rows:
            records = row["runner_records"]
            outcome, terminal = _session_outcome(records)
            status_counts[outcome] += 1
            coverage = None
            if terminal is not None and terminal.get("t") == "SESSION_CLOSED":
                coverage = terminal.get("result", {}).get("opportunity_coverage")
            if isinstance(coverage, dict):
                count = coverage.get("candidate_signal_count")
                if type(count) is int and count >= 0:
                    candidate_count += count
                else:
                    candidate_count_known = False
                planned = coverage.get("planned_instrument_count")
                quoted = coverage.get("quoted_instrument_count")
                if type(planned) is int and planned >= 0 and type(quoted) is int and 0 <= quoted <= planned:
                    coverage_sessions += 1
                    planned_instrument_sessions += planned
                    quoted_instrument_sessions += quoted
                else:
                    coverage_missing_sessions += 1
                cden = coverage.get("candidate_signal_count")
                ccovered = coverage.get("candidate_signal_quote_covered_count")
                if type(cden) is int and cden >= 0 and type(ccovered) is int and 0 <= ccovered <= cden:
                    candidate_quote_sessions += 1
                    candidate_quote_denominator += cden
                    candidate_quote_covered += ccovered
            else:
                coverage_missing_sessions += 1
                candidate_count_known = False
            for event in records:
                if event.get("t") == "PAPER_INTENT":
                    order_intent_count += 1
                elif event.get("t") == "PAPER_SKIP":
                    skip_count += 1
                elif event.get("t") == "PAPER_EXECUTION":
                    execution_count += 1
                    execution = event.get("execution", {})
                    fills = execution.get("fills", []) if isinstance(execution, dict) else []
                    if isinstance(fills, list) and fills:
                        filled_intents.add(str(event.get("intent_id")))
                        fill_ids.update(str(fill.get("fill_id")) for fill in fills if isinstance(fill, dict)
                                        and fill.get("fill_id"))
            archive = row["account_archive"]
            if archive:
                prior = p_and_l_archives.get(row["session_date"])
                if prior is not None and prior["snapshot_sha256"] != archive["snapshot_sha256"]:
                    raise BookPerformanceError("same book/date has conflicting account archives")
                p_and_l_archives[row["session_date"]] = archive

        ordered_archives = [p_and_l_archives[key] for key in sorted(p_and_l_archives)]
        gross_pnl = net_pnl = fees = taxes = Decimal(0)
        last_cumulative = None
        for archive in ordered_archives:
            reconciliation = archive["return_reconciliation"]
            current = {
                "net": _decimal(reconciliation["economic_return_inr"], "economic return"),
                "nav": _decimal(reconciliation["nav_return_inr"], "NAV return"),
                "fees": _decimal(reconciliation["fees_inr"], "fees"),
                "taxes": _decimal(reconciliation["taxes_inr"], "taxes"),
                "gross": (_decimal(reconciliation["realized_price_pnl_inr"], "realized P&L")
                          + _decimal(reconciliation["unrealized_price_pnl_inr"], "unrealized P&L")
                          + _decimal(reconciliation["gross_dividend_income_inr"], "dividend income")
                          + _decimal(reconciliation["gross_cash_yield_income_inr"], "cash yield")),
            }
            prior = last_cumulative or {name: Decimal(0) for name in current}
            delta = {name: current[name] - prior[name] for name in current}
            if delta["fees"] < 0 or delta["taxes"] < 0:
                raise BookPerformanceError("cumulative costs cannot decrease between account snapshots")
            gross_pnl += delta["gross"]
            net_pnl += delta["net"]
            fees += delta["fees"]
            taxes += delta["taxes"]
            last_cumulative = current
        costs = fees + taxes
        books.append({
            "book_id": book_id, "account_id": next(iter(account_ids), None),
            "session_denominator": len(rows), "session_status_counts": status_counts,
            "candidate_opportunity_count": candidate_count if candidate_count_known else None,
            "candidate_count_complete": candidate_count_known,
            "paper_intent_count": order_intent_count, "execution_receipt_count": execution_count,
            "filled_intent_count": len(filled_intents), "unique_fill_count": len(fill_ids),
            "paper_skip_count": skip_count,
            "quote_coverage": {
                "sessions_with_coverage": coverage_sessions,
                "sessions_missing_coverage": coverage_missing_sessions,
                "planned_instrument_denominator": planned_instrument_sessions,
                "quoted_instrument_count": quoted_instrument_sessions,
                "instrument_quote_coverage_ratio": (quoted_instrument_sessions / planned_instrument_sessions
                                                      if planned_instrument_sessions else None),
                "candidate_quote_session_count": candidate_quote_sessions,
                "candidate_signal_denominator": candidate_quote_denominator,
                "candidate_signal_quote_covered": candidate_quote_covered,
                "candidate_quote_coverage_ratio": (candidate_quote_covered / candidate_quote_denominator
                                                    if candidate_quote_denominator else None),
            },
            "account_archive_count": len(ordered_archives),
            "pnl_sessions_missing_account_archive": len(rows) - len(ordered_archives),
            "gross_pnl_contribution_inr": str(gross_pnl) if ordered_archives else None,
            "net_pnl_contribution_inr": str(net_pnl) if ordered_archives else None,
            "fees_inr": str(fees) if ordered_archives else None,
            "taxes_inr": str(taxes) if ordered_archives else None,
            "costs_to_abs_gross_ratio": str(costs / abs(gross_pnl)) if gross_pnl else None,
            "cost_per_filled_intent_inr": str(costs / len(filled_intents)) if filled_intents else None,
            "book_archive_window": ([ordered_archives[0]["session_date"],
                                      ordered_archives[-1]["session_date"]] if ordered_archives else None),
        })
    report = {"schema_version": "1.0", "books": books}
    report["report_sha256"] = hashlib.sha256(_canonical(report)).hexdigest()
    return report


def render_book_performance_report(report: dict) -> str:
    """Human-readable report that retains every session and the explicit quote denominators."""
    if not isinstance(report, dict) or not isinstance(report.get("books"), list):
        raise BookPerformanceError("book report must be generated from book evidence")
    lines = ["Financial Brain — By-Book Paper Performance and Coverage",
             f"Report SHA-256: {report.get('report_sha256', 'MISSING')}"]
    for book in report["books"]:
        statuses = book["session_status_counts"]
        quote = book["quote_coverage"]
        lines.extend([
            f"\nBook: {book['book_id']} | sessions denominator: {book['session_denominator']}",
            f"  outcomes: closed={statuses['CLOSED']}, no-trade={statuses['NO_TRADE']}, "
            f"incomplete={statuses['INCOMPLETE']}, unrecorded={statuses['UNRECORDED']}",
            f"  opportunities: {book['candidate_opportunity_count'] if book['candidate_count_complete'] else 'INCOMPLETE'}; "
            f"paper intents={book['paper_intent_count']}; fills={book['unique_fill_count']}; "
            f"skips={book['paper_skip_count']}",
            f"  quote coverage: instruments={quote['quoted_instrument_count']}/"
            f"{quote['planned_instrument_denominator']}; missing sessions={quote['sessions_missing_coverage']}; "
            f"candidate signals={quote['candidate_signal_quote_covered']}/"
            f"{quote['candidate_signal_denominator']}",
            f"  P&L from {book['account_archive_count']} account snapshots: gross="
            f"{book['gross_pnl_contribution_inr']}, net={book['net_pnl_contribution_inr']}, "
            f"fees={book['fees_inr']}, taxes={book['taxes_inr']}",
            f"  fee/tax per filled intent: {book['cost_per_filled_intent_inr']}; "
            f"P&L sessions missing close archive: {book['pnl_sessions_missing_account_archive']}",
        ])
    lines.append("Reports retain NO_TRADE, incomplete, unrecorded, unfilled, and missing-coverage rows; "
                 "they do not rank only winners or fills.")
    return "\n".join(lines) + "\n"
