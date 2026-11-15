from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.forward_edge.session_state import (
    ForwardRunConfig,
    ForwardSessionStore,
    SessionState,
    SessionStateError,
    session_attempt_key,
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


def test_strategy_attempt_keys_are_durable_and_never_overwrite_prior_attempt(tmp_path):
    first = config(mode="paper", attempt_id="run-001")
    second = config(mode="paper", attempt_id="run-002")
    first_id = session_attempt_key(strategy_sha256=first.strategy_sha256,
                                   exchange_date=first.exchange_date, cohort=first.cohort,
                                   mode=first.mode, attempt_id=first.attempt_id)
    second_id = session_attempt_key(strategy_sha256=second.strategy_sha256,
                                    exchange_date=second.exchange_date, cohort=second.cohort,
                                    mode=second.mode, attempt_id=second.attempt_id)
    assert first_id != second_id
    assert session_attempt_key(strategy_sha256=first.strategy_sha256,
                               exchange_date=first.exchange_date, cohort=first.cohort,
                               mode=first.mode, attempt_id=first.attempt_id) == first_id

    store = ForwardSessionStore(tmp_path / "sessions.sqlite")
    assert store.create(first_id, first, now=NOW)
    assert store.create(second_id, second, now=NOW + timedelta(seconds=1))
    first_digest = store.config_sha256(first_id)
    store.transition(first_id, SessionState.PREFLIGHT, now=NOW + timedelta(seconds=2))
    assert store.create(first_id, first, now=NOW + timedelta(seconds=3)) is False
    assert store.config_sha256(first_id) == first_digest
    assert store.state(first_id) is SessionState.PREFLIGHT
    assert store.state(second_id) is SessionState.PLANNED
    with pytest.raises(SessionStateError, match="different config"):
        store.create(first_id, config(mode="paper", attempt_id="altered"), now=NOW)
    store.close()
