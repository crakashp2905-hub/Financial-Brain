import hashlib
import json

import pytest

from financial_brain.forward_edge.book_performance_report import (
    BookPerformanceError,
    build_book_performance_report,
    render_book_performance_report,
)


def _archive(day, *, gross, fees, taxes=0, account="A"):
    body = {
        "account_id": account,
        "session_date": day,
        "return_reconciliation": {
            "economic_return_inr": str(gross - fees - taxes),
            "nav_return_inr": str(gross - fees - taxes),
            "realized_price_pnl_inr": str(gross),
            "unrealized_price_pnl_inr": "0",
            "gross_dividend_income_inr": "0",
            "gross_cash_yield_income_inr": "0",
            "fees_inr": str(fees),
            "taxes_inr": str(taxes),
            "reconciled": True,
        },
    }
    body["snapshot_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return body


def _closed(session_id, *, status="CLOSED", candidates=2, covered=1, fills=None):
    return {
        "t": "SESSION_CLOSED",
        "session_id": session_id,
        "result": {
            "status": status,
            "opportunity_coverage": {
                "candidate_signal_count": candidates,
                "candidate_signal_quote_covered_count": covered,
                "planned_instrument_count": 4,
                "quoted_instrument_count": 3,
            },
        },
    }


def test_report_keeps_no_trade_incomplete_missing_pnl_and_unfilled_rows_visible():
    rows = [
        {"book_id": "swing", "session_id": "win", "session_date": "2026-11-02",
         "runner_records": [_closed("win"), {"t": "PAPER_INTENT", "session_id": "win"},
                            {"t": "PAPER_EXECUTION", "session_id": "win", "intent_id": "i1",
                             "execution": {"fills": [{"fill_id": "f1"}]}}],
         "account_archive": _archive("2026-11-02", gross=100, fees=10)},
        {"book_id": "swing", "session_id": "loss", "session_date": "2026-11-03",
         "runner_records": [_closed("loss"), {"t": "PAPER_INTENT", "session_id": "loss"}],
         "account_archive": _archive("2026-11-03", gross=160, fees=30)},
        {"book_id": "swing", "session_id": "none", "session_date": "2026-11-04",
         "runner_records": [_closed("none", status="NO_TRADE", candidates=0, covered=0),
                             {"t": "PAPER_SKIP", "session_id": "none"}]},
        {"book_id": "swing", "session_id": "bad", "session_date": "2026-11-05",
         "runner_records": [{"t": "SESSION_INCOMPLETE", "session_id": "bad"}]},
        {"book_id": "swing", "session_id": "absent", "session_date": "2026-11-06",
         "runner_records": []},
    ]
    report = build_book_performance_report(rows)
    book = report["books"][0]
    assert book["session_denominator"] == 5
    assert book["session_status_counts"] == {
        "CLOSED": 2, "NO_TRADE": 1, "INCOMPLETE": 1, "UNRECORDED": 1,
    }
    assert book["candidate_opportunity_count"] is None
    assert book["quote_coverage"]["sessions_missing_coverage"] == 2
    assert book["quote_coverage"]["planned_instrument_denominator"] == 12
    assert book["quote_coverage"]["quoted_instrument_count"] == 9
    assert book["paper_intent_count"] == 2
    assert book["filled_intent_count"] == 1
    assert book["gross_pnl_contribution_inr"] == "160"
    assert book["net_pnl_contribution_inr"] == "130"
    assert book["fees_inr"] == "30"
    assert book["pnl_sessions_missing_account_archive"] == 3
    text = render_book_performance_report(report)
    assert "no-trade=1" in text and "incomplete=1" in text and "unrecorded=1" in text
    assert "sessions denominator: 5" in text


def test_same_cumulative_archive_is_not_double_counted_for_multiple_sessions_same_date():
    archive = _archive("2026-11-02", gross=10, fees=2)
    rows = [
        {"book_id": "intraday", "session_id": session, "session_date": "2026-11-02",
         "runner_records": [_closed(session, candidates=0, covered=0)], "account_archive": archive}
        for session in ("morning", "afternoon")
    ]
    book = build_book_performance_report(rows)["books"][0]
    assert book["session_denominator"] == 2
    assert book["account_archive_count"] == 1
    assert book["gross_pnl_contribution_inr"] == "10"
    assert book["net_pnl_contribution_inr"] == "8"
    assert book["fees_inr"] == "2"


def test_missing_quote_and_pnl_evidence_are_not_imputed_as_zero():
    row = {"book_id": "options", "session_id": "s1", "session_date": "2026-11-02",
           "runner_records": [_closed("s1")], "account_archive": None}
    book = build_book_performance_report([row])["books"][0]
    assert book["gross_pnl_contribution_inr"] is None
    assert book["net_pnl_contribution_inr"] is None
    assert book["quote_coverage"]["sessions_missing_coverage"] == 0
    assert book["quote_coverage"]["candidate_signal_denominator"] == 2
    assert book["quote_coverage"]["candidate_signal_quote_covered"] == 1
    assert book["pnl_sessions_missing_account_archive"] == 1


def test_tampered_archive_is_rejected():
    archive = _archive("2026-11-02", gross=10, fees=2)
    archive["cash"] = "tampered"
    with pytest.raises(BookPerformanceError, match="hash is invalid"):
        build_book_performance_report([{
            "book_id": "swing", "session_id": "s1", "session_date": "2026-11-02",
            "runner_records": [_closed("s1")], "account_archive": archive,
        }])
