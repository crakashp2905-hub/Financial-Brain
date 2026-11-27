import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.paper.options import OptionPaperError, Quote
from financial_brain.paper.option_counterfactuals import (
    audit,
    initialize,
    record_rejected_candidate,
    resolve_counterfactual,
)


DECISION = datetime(2026, 11, 27, 5, tzinfo=timezone.utc)
EXPIRY = date(2026, 12, 3)


def _quote(at, bid=100, ask=101):
    return Quote(44, "NIFTY26DEC25000CE", EXPIRY, 10, at, bid, ask, 100, 100)


def _ledger(monkeypatch):
    monkeypatch.setenv("FB_OPTIONS_ARCHIVE_LICENSE_APPROVED", "1")
    con = sqlite3.connect(":memory:")
    initialize(con)
    return con


def _record(con):
    return record_rejected_candidate(
        con, candidate_id="candidate-1", decision_at=DECISION,
        planned_exit_at=DECISION + timedelta(minutes=5),
        rejection_reasons=("SPREAD_EXCEEDS_POLICY",),
        best_quote=_quote(DECISION - timedelta(seconds=1)),
        quote_source_sha256="a" * 64, rights_reference="kite-retention-approval",
        event_id="reject-1",
    )


def test_rejected_candidate_and_future_outcome_are_append_only_and_not_trades(monkeypatch):
    con = _ledger(monkeypatch)
    digest = _record(con)
    assert len(digest) == 64
    exit_at = DECISION + timedelta(minutes=5)
    outcome_digest = resolve_counterfactual(
        con, candidate_id="candidate-1", exit_at=exit_at,
        exit_quote=_quote(exit_at, bid=110, ask=111), event_id="resolve-1")
    assert len(outcome_digest) == 64
    assert audit(con) == {"events": 2, "invalid_events": [], "rejected_candidates": 1,
                          "counterfactual_resolutions": 1, "rights_gate_required": True,
                          "physical_trade_created": False}
    with pytest.raises(OptionPaperError, match="already resolved"):
        resolve_counterfactual(con, candidate_id="candidate-1", exit_at=exit_at,
                               exit_quote=None, event_id="resolve-2")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        con.execute("UPDATE option_candidate_events SET payload_json='{}'")


def test_counterfactual_requires_rights_preentry_and_the_declared_horizon(monkeypatch):
    monkeypatch.delenv("FB_OPTIONS_ARCHIVE_LICENSE_APPROVED", raising=False)
    con = sqlite3.connect(":memory:")
    initialize(con)
    with pytest.raises(OptionPaperError, match="rights"):
        _record(con)
    monkeypatch.setenv("FB_OPTIONS_ARCHIVE_LICENSE_APPROVED", "1")
    with pytest.raises(OptionPaperError, match="known then"):
        record_rejected_candidate(
            con, candidate_id="future", decision_at=DECISION,
            planned_exit_at=DECISION + timedelta(minutes=1),
            rejection_reasons=("NO_EDGE",), best_quote=_quote(DECISION + timedelta(seconds=1)),
            quote_source_sha256="a" * 64, rights_reference="rights", event_id="bad")
    _record(con)
    with pytest.raises(OptionPaperError, match="before the predeclared horizon"):
        resolve_counterfactual(con, candidate_id="candidate-1",
                               exit_at=DECISION + timedelta(minutes=4),
                               exit_quote=None, event_id="early")


def test_missing_future_quote_is_recorded_as_unresolved_not_a_zero_loss(monkeypatch):
    con = _ledger(monkeypatch)
    _record(con)
    resolve_counterfactual(con, candidate_id="candidate-1",
                           exit_at=DECISION + timedelta(minutes=6),
                           exit_quote=None, event_id="no-exit")
    body = con.execute("SELECT payload_json FROM option_candidate_events "
                       "WHERE event_type='COUNTERFACTUAL_RESOLVED'").fetchone()[0]
    assert '"status":"NO_EXIT_QUOTE"' in body
    assert '"outcome":null' in body
