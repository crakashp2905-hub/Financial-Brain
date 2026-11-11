"""Research registry (FB-0144, FB-0153, FB-0158, FB-0162, FB-0163).

* ``HypothesisLedger`` is append-only. A hypothesis gets an immutable id derived from family and name, so a duplicate
  registration is refused rather than overwritten. Failed and negative results are kept and never deleted.
* ``search`` finds past hypotheses and trial failures by keyword across family, name and notes.
* ``evidence_label`` gives the evidence tier of a result. Prospective evidence outranks out-of-sample history, and
  out-of-sample history outranks in-sample results.
* ``split_report`` groups returns by a label (regime, year, tier, asset class) so results can be read per group.
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from statistics import mean


@dataclass(frozen=True)
class Hypothesis:
    hypothesis_id: str
    family: str
    name: str
    notes: str = ""
    outcome: str = "PENDING"  # PENDING | PASSED | FAILED


@dataclass
class HypothesisLedger:
    _entries: dict[str, Hypothesis] = field(default_factory=dict)

    def register(self, family: str, name: str, notes: str = "") -> Hypothesis:
        if not family or not name:
            raise ValueError("family and name are required")
        hid = hashlib.sha256(f"{family}|{name}".encode()).hexdigest()[:16]
        if hid in self._entries:
            raise ValueError(f"hypothesis {family}/{name} already registered")
        entry = Hypothesis(hid, family, name, notes)
        self._entries[hid] = entry
        return entry

    def record_outcome(self, hypothesis_id: str, outcome: str, note: str = "") -> None:
        if outcome not in ("PASSED", "FAILED"):
            raise ValueError("outcome must be PASSED or FAILED")
        current = self._entries.get(hypothesis_id)
        if current is None:
            raise KeyError(hypothesis_id)
        if current.outcome != "PENDING":
            raise ValueError("outcome already recorded; append a correction instead")
        joined = f"{current.notes} | {note}".strip(" |") if note else current.notes
        self._entries[hypothesis_id] = Hypothesis(current.hypothesis_id, current.family, current.name, joined, outcome)

    def get(self, hypothesis_id: str) -> Hypothesis:
        return self._entries[hypothesis_id]

    def failures(self) -> list[Hypothesis]:
        return [h for h in self._entries.values() if h.outcome == "FAILED"]

    def search(self, text: str) -> list[Hypothesis]:
        needle = text.lower()
        return [h for h in self._entries.values() if needle in f"{h.family} {h.name} {h.notes}".lower()]


TIERS = ("PROSPECTIVE", "HISTORICAL_OOS", "IN_SAMPLE")


def evidence_label(out_of_sample: bool, prospective: bool) -> str:
    if prospective:
        return "PROSPECTIVE"
    if out_of_sample:
        return "HISTORICAL_OOS"
    return "IN_SAMPLE"


def split_report(returns: Sequence[float], labels: Sequence[str]) -> dict[str, float]:
    if len(returns) != len(labels):
        raise ValueError("returns and labels must align")
    groups: dict[str, list[float]] = {}
    for r, lab in zip(returns, labels, strict=True):
        groups.setdefault(lab, []).append(r)
    return {lab: mean(vals) for lab, vals in sorted(groups.items())}
