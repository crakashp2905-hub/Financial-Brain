"""Canonical labels separating research, paper, forward and independently attested evidence."""
from __future__ import annotations

from enum import Enum


class EvidenceClass(str, Enum):
    EXPLORATORY_SHADOW = "EXPLORATORY_SHADOW"
    HISTORICAL_REPLAY = "HISTORICAL_REPLAY"
    DETERMINISTIC_FIXTURE = "DETERMINISTIC_FIXTURE"
    LOCAL_PAPER = "LOCAL_PAPER"
    PROSPECTIVELY_OBSERVED_SESSION = "PROSPECTIVELY_OBSERVED_SESSION"
    INDEPENDENTLY_ATTESTED_FORWARD = "INDEPENDENTLY_ATTESTED_FORWARD"
