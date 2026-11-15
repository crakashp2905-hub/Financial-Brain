from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.forward_edge.session_state import (
    ForwardRunConfig,
    ForwardSessionStore,
    SessionState,
    SessionStateError,
)


NOW = datetime(2026, 11, 13, 3, 45, tzinfo=timezone.utc)


def config(**changes):
    values = {"exchange_date": date(2026, 11, 13), "cohort": "momentum-v1",
              "strategy_sha256": "a" * 64, "benchmark": "NIFTY50_TRI",
              "instrument_universe": ("NSE:INFY", "NSE:RELIANCE"),
              "capital": Decimal("100000.00"), "constraints": {"max_risk": 1000, "max_names": 2},
              "ledger_id": "paper-ledger-1"}
    values.update(changes)
    return ForwardRunConfig(**values)


def test_config_hash_is_canonical_across_universe_and_mapping_order():
    left = config()
    right = config(instrument_universe=("NSE:RELIANCE", "NSE:INFY"),
                   capital=Decimal("100000"), constraints={"max_names": 2, "max_risk": 1000})
    assert left.canonical_json() == right.canonical_json()
    assert left.sha256() == right.sha256()


def test_session_creation_is_durable_idempotent_and_metadata_bound(tmp_path):
    path = tmp_path / "sessions.sqlite"
    store = ForwardSessionStore(path)
    assert store.create("session-1", config(), now=NOW) is True
    digest = store.config_sha256("session-1")
    store.close()
    resumed = ForwardSessionStore(path)
    assert resumed.create("session-1", config(), now=NOW + timedelta(seconds=1)) is False
    assert resumed.config_sha256("session-1") == digest
    with pytest.raises(SessionStateError, match="different config"):
        resumed.create("session-1", config(cohort="other"), now=NOW)
    resumed.close()


def test_state_machine_rejects_invalid_transitions_and_terminal_reopening(tmp_path):
    store = ForwardSessionStore(tmp_path / "sessions.sqlite")
    store.create("s", config(), now=NOW)
    with pytest.raises(SessionStateError, match="PLANNED -> OPEN"):
        store.transition("s", SessionState.OPEN, now=NOW)
    store.transition("s", SessionState.PREFLIGHT, now=NOW)
    store.transition("s", SessionState.OPEN, now=NOW + timedelta(seconds=1))
    store.transition("s", SessionState.DRAINING, now=NOW + timedelta(seconds=2))
    store.transition("s", SessionState.CLOSED, now=NOW + timedelta(seconds=3))
    assert store.state("s") is SessionState.CLOSED
    with pytest.raises(SessionStateError, match="CLOSED -> OPEN"):
        store.transition("s", SessionState.OPEN, now=NOW + timedelta(seconds=4))
    store.close()


def test_transitions_are_reasoned_idempotent_and_clock_monotonic(tmp_path):
    store = ForwardSessionStore(tmp_path / "sessions.sqlite")
    store.create("s", config(), now=NOW)
    event_time = NOW + timedelta(seconds=1)
    assert store.transition("s", SessionState.PREFLIGHT, now=event_time, event_id="preflight-1") is True
    assert store.transition("s", SessionState.PREFLIGHT, now=event_time, event_id="preflight-1") is False
    with pytest.raises(SessionStateError, match="different session transition"):
        store.transition("s", SessionState.PREFLIGHT, now=event_time, reason="altered", event_id="preflight-1")
    with pytest.raises(SessionStateError, match="requires a reason"):
        store.transition("s", SessionState.REJECTED, now=event_time)
    with pytest.raises(SessionStateError, match="move backwards"):
        store.transition("s", SessionState.OPEN, now=NOW)
    store.close()
