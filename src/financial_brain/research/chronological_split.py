"""Chronological purged splits for overlapping labels and shared decision cross-sections."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


class ChronologicalSplitError(ValueError):
    """Invalid timeline or split boundaries that could leak outcomes."""


def _utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ChronologicalSplitError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class LabelObservation:
    observation_id: str
    decision_group_id: str
    decision_at: datetime
    label_end_at: datetime

    def __post_init__(self) -> None:
        if not all(isinstance(v, str) and v.strip() for v in (self.observation_id, self.decision_group_id)):
            raise ChronologicalSplitError("observation and decision-group IDs are required")
        decision = _utc(self.decision_at, "decision timestamp")
        end = _utc(self.label_end_at, "label end timestamp")
        if end < decision:
            raise ChronologicalSplitError("label end cannot precede its decision")


@dataclass(frozen=True)
class ChronologicalSplit:
    train_ids: tuple[str, ...]
    test_ids: tuple[str, ...]
    purged_ids: tuple[str, ...]
    test_start: datetime
    test_end: datetime
    embargo: timedelta

    def __post_init__(self) -> None:
        if set(self.train_ids) & set(self.test_ids) or set(self.train_ids) & set(self.purged_ids):
            raise ChronologicalSplitError("split partitions must be disjoint")


def chronological_purged_split(
    observations: tuple[LabelObservation, ...],
    *,
    test_start: datetime,
    test_end: datetime,
    embargo: timedelta = timedelta(0),
) -> ChronologicalSplit:
    """Make a forward-only split and purge labels ending inside the embargo before the test.

    `decision_group_id` identifies rows that share one decision event (for example, every
    instrument in a cross-section or all labels emitted for one 15-minute forecast). All rows
    in a group necessarily share a decision timestamp and therefore cannot straddle the split.
    """
    start, end = _utc(test_start, "test start"), _utc(test_end, "test end")
    if end <= start:
        raise ChronologicalSplitError("test window must have positive duration")
    if not isinstance(embargo, timedelta) or embargo < timedelta(0):
        raise ChronologicalSplitError("embargo must be a nonnegative duration")
    ids = [row.observation_id for row in observations]
    if len(ids) != len(set(ids)):
        raise ChronologicalSplitError("observation IDs must be unique")
    group_times: dict[str, set[datetime]] = {}
    for row in observations:
        group_times.setdefault(row.decision_group_id, set()).add(_utc(row.decision_at, "decision timestamp"))
    if any(len(times) != 1 for times in group_times.values()):
        raise ChronologicalSplitError("a shared decision group cannot span timestamps")

    train: list[str] = []
    test: list[str] = []
    purged: list[str] = []
    cutoff = start - embargo
    for row in observations:
        decision = _utc(row.decision_at, "decision timestamp")
        label_end = _utc(row.label_end_at, "label end timestamp")
        if start <= decision < end:
            test.append(row.observation_id)
        elif decision < start:
            if label_end < cutoff:
                train.append(row.observation_id)
            else:
                purged.append(row.observation_id)
    test_groups = {row.decision_group_id for row in observations if row.observation_id in set(test)}
    if any(row.decision_group_id in test_groups and row.observation_id in train for row in observations):
        raise ChronologicalSplitError("shared cross-section was divided between train and test")
    if not train or not test:
        raise ChronologicalSplitError("split must contain at least one training and one test observation")
    return ChronologicalSplit(tuple(sorted(train)), tuple(sorted(test)), tuple(sorted(purged)), start, end, embargo)


__all__ = ["ChronologicalSplit", "ChronologicalSplitError", "LabelObservation", "chronological_purged_split"]
