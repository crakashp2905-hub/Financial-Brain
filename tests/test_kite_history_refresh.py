"""Incremental Kite history refresh refuses missing sessions and reports failed pulls."""
from datetime import date

import duckdb
import pytest

from financial_brain import cli
from financial_brain.ingest import kite_history as KH
from financial_brain.providers import kite_data as KD


def test_missing_session_is_refused_before_opening_history_database(monkeypatch):
    def missing():
        raise KD.KiteNotConfigured("login required")

    monkeypatch.setattr(KD, "credentials", missing)
    monkeypatch.setattr(duckdb, "connect", lambda *args, **kwargs: pytest.fail("database opened before preflight"))
    with pytest.raises(KD.KiteNotConfigured, match="login required"):
        cli.main(["kite-history", "minute", "--start", "2026-10-01"])


def test_bounded_refresh_passes_start_date_and_exposes_partial_failure(monkeypatch):
    monkeypatch.setattr(KD, "credentials", lambda: ("key", "token"))
    real_connect = duckdb.connect
    monkeypatch.setattr(duckdb, "connect", lambda *args, **kwargs: real_connect(":memory:"))
    monkeypatch.setattr(KH, "minute_universe", lambda con, top: [("ABC", "ISIN")])
    seen = {}

    def run_minute(names, **kwargs):
        seen.update(names=names, **kwargs)
        return {"failed": 1, "done": 0, "missing": 0, "stored": 0}

    monkeypatch.setattr(KH, "run_minute", run_minute)
    assert cli.main(["kite-history", "minute", "--top", "300", "--start", "2026-10-01"]) == 1
    assert seen["names"] == [("ABC", "ISIN")]
    assert seen["start"] == date(2026, 10, 1)
