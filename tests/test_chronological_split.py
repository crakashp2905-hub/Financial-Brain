from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.research.chronological_split import (
    ChronologicalSplitError, LabelObservation, chronological_purged_split,
)


def dt(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 1, day, hour, minute, tzinfo=timezone.utc)


def row(name: str, group: str, day: int, end_day: int, *, minute: int = 0, end_hour: int = 0) -> LabelObservation:
    end_time = dt(end_day, (10 if end_day == day and end_hour == 0 else end_hour), minute if end_day == day else 0)
    return LabelObservation(name, group, dt(day, 10, minute), end_time)


def test_purges_overlapping_labels_and_embargoes_boundary() -> None:
    data = (row("old-safe", "g1", 1, 2), row("overlap", "g2", 2, 4), row("in-embargo", "g3", 3, 4),
            row("test", "g4", 4, 5))
    split = chronological_purged_split(data, test_start=dt(4), test_end=dt(5), embargo=timedelta(days=1))
    assert split.train_ids == ("old-safe",)
    assert split.purged_ids == ("in-embargo", "overlap")
    assert split.test_ids == ("test",)


def test_shared_cross_section_remains_whole_on_test_side() -> None:
    data = (row("prior", "prior", 1, 2), row("symbol-a", "cross-section-1", 3, 3),
            row("symbol-b", "cross-section-1", 3, 3), row("test", "test", 4, 4))
    split = chronological_purged_split(data, test_start=dt(3), test_end=dt(5))
    assert split.test_ids == ("symbol-a", "symbol-b", "test")
    assert not set(split.train_ids).intersection(split.test_ids)


def test_overlapping_fifteen_minute_labels_are_purged_by_interval_end() -> None:
    data = (row("prior-safe", "prior", 1, 1, end_hour=23),
            row("forecast-1", "09:45-cross-section", 1, 2, minute=45, end_hour=11),
            row("forecast-2", "10:00-cross-section", 1, 2, minute=0, end_hour=12),
            row("test", "next-session", 2, 3))
    split = chronological_purged_split(data, test_start=dt(2), test_end=dt(3))
    assert split.purged_ids == ("forecast-1", "forecast-2")


def test_naive_clock_and_duplicate_ids_are_rejected() -> None:
    with pytest.raises(ChronologicalSplitError, match="timezone-aware"):
        LabelObservation("x", "g", datetime(2026, 1, 1), datetime(2026, 1, 2))
    duplicate = row("x", "g1", 1, 1)
    with pytest.raises(ChronologicalSplitError, match="unique"):
        chronological_purged_split((duplicate, duplicate), test_start=dt(2), test_end=dt(3))


def test_group_cannot_claim_two_decision_times() -> None:
    data = (row("a", "same", 1, 1), row("b", "same", 2, 2), row("t", "t", 3, 3))
    with pytest.raises(ChronologicalSplitError, match="cannot span timestamps"):
        chronological_purged_split(data, test_start=dt(3), test_end=dt(4))


def test_empty_training_partition_is_rejected() -> None:
    with pytest.raises(ChronologicalSplitError, match="at least one"):
        chronological_purged_split((row("t", "t", 4, 4),), test_start=dt(4), test_end=dt(5))
