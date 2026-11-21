from __future__ import annotations

import pytest

from financial_brain.kernel.engine_extra import (
    AccountScopedStore,
    IsolationError,
    LatencyModel,
    LinkMode,
    pessimistic_fill,
    read_event,
    stress_run,
)


def test_v1_and_v2_events_read_to_the_same_shape() -> None:
    v1 = read_event({"schema_version": 1, "instrument": "X", "px": 10.5, "time": "t"})
    v2 = read_event({"schema_version": 2, "instrument": "X", "price": 10.5, "time": "t"})
    assert v1 == v2


def test_unknown_schema_version_and_missing_fields_are_refused() -> None:
    with pytest.raises(ValueError, match="unknown"):
        read_event({"schema_version": 9})
    with pytest.raises(ValueError, match="px"):
        read_event({"schema_version": 1, "instrument": "X", "time": "t"})


def test_outage_delivers_nothing_and_normal_delivers_everything() -> None:
    counts = stress_run(events=200, base_ms=5, jitter_ms=2, loss_rate=0.2, seed=1)
    assert counts["OUTAGE"] == 0
    assert counts["NORMAL"] == 200
    assert 0 < counts["DEGRADED"] < 200


def test_degraded_latency_is_higher_than_normal() -> None:
    normal = LatencyModel(5, 2, 0.0, seed=3).deliver(LinkMode.NORMAL)
    degraded = LatencyModel(5, 2, 0.0, seed=3).deliver(LinkMode.DEGRADED)
    assert normal is not None and degraded is not None and degraded > normal


def test_accounts_cannot_read_each_others_strategy_state() -> None:
    store = AccountScopedStore()
    store.put("A1", "orb", {"open": 1})
    assert store.get("A1", "orb") == {"open": 1}
    with pytest.raises(IsolationError):
        store.get("A2", "orb")


def test_pessimistic_fills_stay_on_the_adverse_half_and_are_seeded() -> None:
    buy = pessimistic_fill("BUY", 90.0, 110.0, seed=4)
    sell = pessimistic_fill("SELL", 90.0, 110.0, seed=4)
    assert 100.0 <= buy <= 110.0
    assert 90.0 <= sell <= 100.0
    assert buy == pessimistic_fill("BUY", 90.0, 110.0, seed=4)
    with pytest.raises(ValueError):
        pessimistic_fill("HOLD", 90.0, 110.0, seed=4)
