"""Announcement classifier benchmark (P2-3): precision on hand-checked samples.

Two labelled files in ``tests/fixtures``:

``announcement_labels.json``          168 announcements v1 called high-materiality,
                                      12 per type. Used to *tune* v2 - so its score is
                                      in-sample and flatters the rules.
``announcement_labels_holdout.json``  102 drawn *after* v2 was written, 6 per type, on
                                      days the first sample did not touch. Its score
                                      before any change it prompted (96/102, 94.1%) is
                                      the honest estimate.

A row is correct when both the event type and the high/not-high call match the label.
Labels were made by Claude reading headline, subject and BSE subcategory; the owner may
overrule any of them by editing ``gold_type`` / ``gold_high``.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from ..events.classify import HIGH, classify

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
SETS = {"tuning": "announcement_labels.json", "holdout": "announcement_labels_holdout.json"}


def score(name: str) -> dict:
    rows = json.loads((FIXTURES / SETS[name]).read_text(encoding="utf-8"))["rows"]
    per_type: Counter = Counter()
    right: Counter = Counter()
    misses = []
    for r in rows:
        kind, mat, _ = classify(r["category"], r["subcategory"], r["headline"], r["subject"])
        per_type[kind] += 1
        if kind == r["gold_type"] and (mat == HIGH) == r["gold_high"]:
            right[kind] += 1
        else:
            misses.append({"news_id": r["news_id"], "predicted": kind, "gold": r["gold_type"],
                           "note": r.get("note", "")})
    return {"set": name, "n": len(rows), "correct": sum(right.values()),
            "precision": sum(right.values()) / len(rows) if rows else float("nan"),
            "by_type": {k: (right[k], per_type[k]) for k in sorted(per_type)},
            "misses": misses}
