from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import threading

from financial_brain.forward_edge.persistent_killswitch import PersistentKillSwitch


def test_concurrent_desk_loss_trips_append_one_valid_chain(tmp_path):
    path = tmp_path / "shared-kill.sqlite"
    workers = 12
    PersistentKillSwitch(path).close()  # initialize the schema before desk workers start
    barrier = threading.Barrier(workers)
    now = datetime(2026, 11, 27, 12, tzinfo=timezone.utc)

    def trip(index: int) -> bool:
        switch = PersistentKillSwitch(path)
        try:
            barrier.wait(timeout=10)
            return switch.trip(
                event_id=f"desk-loss-{index}", reason="DAILY_LOSS", occurred_at=now,
                evidence_hash=f"{index + 1:064x}", actor_id=f"risk-engine-{index}", actor_type="system",
            )
        finally:
            switch.close()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        assert all(pool.map(trip, range(workers)))
    reader = PersistentKillSwitch(path)
    try:
        assert reader.active
        assert reader.active_reasons == ("DAILY_LOSS",)
        rows = reader._con.execute("SELECT seq FROM kill_events ORDER BY seq").fetchall()
        assert rows == [(index,) for index in range(1, workers + 1)]
        assert reader._replay()[2] == workers
    finally:
        reader.close()


def test_replayed_concurrent_loss_trip_is_idempotent(tmp_path):
    path = tmp_path / "idempotent-kill.sqlite"
    workers = 8
    PersistentKillSwitch(path).close()
    barrier = threading.Barrier(workers)
    at = datetime(2026, 11, 27, 12, tzinfo=timezone.utc)

    def trip(_index: int) -> bool:
        switch = PersistentKillSwitch(path)
        try:
            barrier.wait(timeout=10)
            return switch.trip(event_id="same-loss", reason="DAILY_LOSS", occurred_at=at,
                               evidence_hash="a" * 64)
        finally:
            switch.close()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(trip, range(workers)))
    assert sum(results) == 1
    reader = PersistentKillSwitch(path)
    try:
        assert reader._con.execute("SELECT COUNT(*) FROM kill_events").fetchone()[0] == 1
        assert reader.active
    finally:
        reader.close()
