from datetime import datetime, timezone
import sqlite3

import pytest

from financial_brain.research.alpha_hypothesis_ledger import (
    AlphaHypothesis, HypothesisLedgerError, audit, connect, register,
)


def hypothesis(**changes):
    values = dict(
        hypothesis_id="nse-results-drift", version="v1",
        mechanism="Delayed analyst updates after earnings surprises",
        counterparty="Investors constrained by slow coverage and mandate turnover",
        information_friction="Quarterly result is published after market close; revisions diffuse over sessions",
        expected_horizon="One to ten trading sessions after first executable open",
        falsification_conditions=("No positive sector-adjusted return after costs",),
        source_commit="a" * 40, source_sha256="b" * 64)
    values.update(changes)
    return AlphaHypothesis(**values)


def test_ledger_persists_mechanism_counterparty_delay_horizon_and_falsification():
    con = connect(":memory:")
    digest = register(con, hypothesis(), recorded_at=datetime(2026, 11, 27, tzinfo=timezone.utc))
    assert len(digest) == 64
    assert audit(con) == {"count": 1, "invalid": [], "external_timestamp_attested": False,
                          "result_records_included": False}
    body = con.execute("SELECT body FROM alpha_hypotheses").fetchone()[0]
    assert '"counterparty"' in body and '"information_friction"' in body
    assert '"falsification_conditions"' in body and '"expected_horizon"' in body


def test_hypothesis_versions_are_immutable_and_cannot_be_replaced():
    con = connect(":memory:")
    register(con, hypothesis())
    with pytest.raises(HypothesisLedgerError, match="already registered"):
        register(con, hypothesis())
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        con.execute("UPDATE alpha_hypotheses SET body='{}'")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        con.execute("DELETE FROM alpha_hypotheses")


@pytest.mark.parametrize("change", [
    {"counterparty": ""}, {"information_friction": ""}, {"expected_horizon": ""},
    {"falsification_conditions": ()}, {"source_commit": "short"},
    {"source_sha256": "invalid"},
])
def test_incomplete_hypothesis_is_refused(change):
    con = connect(":memory:")
    with pytest.raises(HypothesisLedgerError):
        register(con, hypothesis(**change))


def test_record_timestamp_requires_timezone():
    with pytest.raises(HypothesisLedgerError, match="timezone-aware"):
        register(connect(":memory:"), hypothesis(), recorded_at=datetime(2026, 11, 27))
