"""Learning pipeline and job tools (FB-0421, FB-0432, FB-0478, FB-0479, FB-0480).

* ``LearningEvent`` is a versioned record of a prediction and, once known, its realised outcome. A realised outcome
  cannot be stamped before the horizon ends.
* ``materialize_labels`` returns only labels whose horizon has completed by ``now``. Pending predictions are never
  filled with a guess.
* ``windows`` builds train and test index ranges. An embargo gap separates train end from test start, so no
  training row's horizon overlaps the test period.
* ``run_bounded`` runs tasks with a fixed worker limit and returns results in input order.
* ``Job`` moves QUEUED -> RUNNING -> DONE, or to CANCELLED from QUEUED or RUNNING. A finished job cannot be cancelled.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class LearningEvent:
    prediction_id: str
    instrument: str
    made_at: datetime
    horizon_end: datetime
    predicted: float
    realized: float | None = None
    realized_at: datetime | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name, value in (("made_at", self.made_at), ("horizon_end", self.horizon_end)):
            if value.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.horizon_end <= self.made_at:
            raise ValueError("horizon must end after the prediction")
        if self.realized is not None:
            if self.realized_at is None or self.realized_at < self.horizon_end:
                raise ValueError("realized outcome cannot be stamped before the horizon ends")


def materialize_labels(events: Sequence[LearningEvent], now: datetime) -> list[LearningEvent]:
    return [e for e in events if e.realized is not None and e.horizon_end <= now]


def windows(
    n: int, train: int, test: int, embargo: int, mode: str
) -> list[tuple[range, range]]:
    if mode not in ("rolling", "expanding"):
        raise ValueError("mode must be rolling or expanding")
    if min(train, test) < 1 or embargo < 0:
        raise ValueError("train and test must be positive, embargo non-negative")
    out: list[tuple[range, range]] = []
    start = 0
    while True:
        train_end = start + train
        test_start = train_end + embargo
        test_end = test_start + test
        if test_end > n:
            break
        train_range = range(0 if mode == "expanding" else start, train_end)
        out.append((train_range, range(test_start, test_end)))
        start += test
    return out


def run_bounded(tasks: Sequence[Callable[[], T]], max_workers: int) -> list[T]:
    if max_workers < 1:
        raise ValueError("max_workers must be positive")
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(task) for task in tasks]
        return [f.result() for f in futures]


class JobState(Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


@dataclass
class Job:
    job_id: str
    state: JobState = JobState.QUEUED

    def start(self) -> None:
        if self.state is not JobState.QUEUED:
            raise ValueError(f"cannot start from {self.state.value}")
        self.state = JobState.RUNNING

    def finish(self) -> None:
        if self.state is not JobState.RUNNING:
            raise ValueError(f"cannot finish from {self.state.value}")
        self.state = JobState.DONE

    def cancel(self) -> None:
        if self.state not in (JobState.QUEUED, JobState.RUNNING):
            raise ValueError(f"cannot cancel from {self.state.value}")
        self.state = JobState.CANCELLED
