from __future__ import annotations

import json
from pathlib import Path

TRACKER = Path(__file__).resolve().parents[1] / "docs" / "audits" / "unified-platform-tracker.json"


def test_tracker_holds_every_checklist_identifier_exactly_once() -> None:
    data = json.loads(TRACKER.read_text(encoding="utf-8"))
    ids = [t["id"] for t in data["tasks"]]
    expected = {f"FB-{n:04d}" for n in range(1, 712)}
    assert len(ids) == len(set(ids)) == 711
    assert set(ids) == expected
    assert data["total"] == 711
