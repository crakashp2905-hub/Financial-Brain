import json
import sqlite3
from datetime import datetime, timezone

import pytest

from financial_brain.interfaces.tradingview_ingress import IngressRejected, accept_alert


NOW = datetime(2026, 10, 30, 5, 0, tzinfo=timezone.utc)
TOKEN = "a" * 48
PAYLOAD = {"alert_id": "event-1", "strategy": "paper-v1", "exchange": "NSE",
           "symbol": "INFY", "direction": "UP", "event_time": NOW.isoformat(),
           "reference_price": 1500.0}


def call(tmp_path, payload=PAYLOAD, token=TOKEN):
    return accept_alert(json.dumps(payload).encode(), supplied_token=token,
                        configured_token=TOKEN, database=tmp_path / "inbox.sqlite",
                        allowed_strategies=frozenset({"paper-v1"}), received_time=NOW)


def test_accept_and_idempotent_retry(tmp_path):
    assert call(tmp_path)[0] == "accepted"
    assert call(tmp_path)[0] == "duplicate"
    with sqlite3.connect(tmp_path / "inbox.sqlite") as db:
        assert db.execute("SELECT count(*) FROM tradingview_alerts").fetchone()[0] == 1
        assert TOKEN not in repr(db.execute("SELECT * FROM tradingview_alerts").fetchall())


def test_wrong_token_creates_no_database(tmp_path):
    with pytest.raises(IngressRejected):
        call(tmp_path, token="wrong")
    assert not (tmp_path / "inbox.sqlite").exists()


def test_conflicting_replay_is_rejected_without_overwrite(tmp_path):
    call(tmp_path)
    with pytest.raises(IngressRejected):
        call(tmp_path, {**PAYLOAD, "reference_price": 1501.0})
    with sqlite3.connect(tmp_path / "inbox.sqlite") as db:
        assert db.execute("SELECT reference_price FROM tradingview_alerts").fetchone()[0] == 1500.0


def test_stale_event_not_persisted(tmp_path):
    with pytest.raises(IngressRejected):
        call(tmp_path, {**PAYLOAD, "event_time": "2026-10-30T04:00:00+00:00"})
    assert not (tmp_path / "inbox.sqlite").exists()


