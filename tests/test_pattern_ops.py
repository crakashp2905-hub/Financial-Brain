"""Operations tests: atomic write, cache, idempotence, redacted logs, limits and schema loading (CP-0129, CP-0366..0377)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from financial_brain.patterns.events_store import EventLog
from financial_brain.patterns.ops import (
    MAX_BARS,
    ResultCache,
    atomic_write_text,
    configure_log,
    enforce_limits,
    load_event,
    record_idempotently,
)
from financial_brain.patterns.spec import EventState, PatternEvent


def _event(eid: str = "pev_1") -> PatternEvent:
    return PatternEvent(
        event_id=eid, spec_sha256="0" * 64, lineage="L1", timeframe="1D",
        candidate_known_at_utc="2026-01-05T10:00:00+00:00", status=EventState.CANDIDATE, data_sha256="1" * 64,
    )


def test_atomic_write_is_utf8_and_leaves_no_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "folder with spaces" / "ünïcode.txt"
    atomic_write_text(target, "naïve café ✓\n")
    assert target.read_text(encoding="utf-8") == "naïve café ✓\n"
    assert [p.name for p in target.parent.iterdir()] == ["ünïcode.txt"]


def test_cache_is_valid_only_for_its_manifest_hash() -> None:
    cache = ResultCache()
    cache.put("manA", "scan", [1, 2])
    assert cache.get("manA", "scan") == [1, 2]
    assert cache.get("manB", "scan") is None  # changed input never reuses output
    cache.invalidate_except("manB")
    assert cache.get("manA", "scan") is None


def test_rerunning_a_scan_appends_nothing_new() -> None:
    log = EventLog()
    events = [_event("pev_1"), _event("pev_2")]
    assert record_idempotently(log, events) == 2
    assert record_idempotently(log, events) == 0
    assert len(log) == 2


def test_logs_are_redacted_and_rotate(tmp_path: Path) -> None:
    path = tmp_path / "patterns.log"
    logger: logging.Logger = configure_log(path, max_bytes=200, backups=2)
    for _ in range(40):
        logger.info("token=ABCDEF123 account 987654321 path C:\\Users\\me\\x.txt")
    for handler in logger.handlers:
        handler.flush()
    text = "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("patterns.log*"))
    assert "ABCDEF123" not in text and "987654321" not in text
    assert (tmp_path / "patterns.log.1").exists()  # rotated


def test_resource_limits_refuse_oversized_inputs() -> None:
    enforce_limits(bars=MAX_BARS, symbols=10)
    with pytest.raises(ValueError, match="bars"):
        enforce_limits(bars=MAX_BARS + 1, symbols=10)


def test_old_record_loads_with_defaults() -> None:
    legacy = {
        "event_id": "pev_old", "spec_sha256": "0" * 64, "lineage": "L1", "timeframe": "1D",
        "candidate_known_at_utc": "2026-01-05T10:00:00+00:00", "status": "CANDIDATE", "data_sha256": "1" * 64,
    }
    event = load_event(legacy)
    assert event.decision_at_utc is None and event.status is EventState.CANDIDATE


def test_newer_record_with_unknown_fields_is_refused_not_dropped() -> None:
    newer = {
        "event_id": "pev_new", "spec_sha256": "0" * 64, "lineage": "L1", "timeframe": "1D",
        "candidate_known_at_utc": "2026-01-05T10:00:00+00:00", "status": "CANDIDATE", "data_sha256": "1" * 64,
        "future_field": 1,
    }
    with pytest.raises(ValueError, match="refusing"):
        load_event(newer)
    with pytest.raises(ValueError, match="missing"):
        load_event({"event_id": "x"})
