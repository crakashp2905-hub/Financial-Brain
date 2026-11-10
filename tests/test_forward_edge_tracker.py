"""Tracker integrity (FE-0149): every status claim is checkable, DONE items cite real tests, blockers are named."""
from __future__ import annotations

import json
import re
from pathlib import Path

from financial_brain.forward_edge import tracker

REPO = Path(__file__).resolve().parents[1]


def _test_names() -> set[str]:
    names: set[str] = set()
    for path in (REPO / "tests").glob("test_forward_edge_*.py"):
        names |= set(re.findall(r"^def (test_\w+)", path.read_text(encoding="utf-8"), re.M))
    return names


def test_all_152_items_are_present_once_and_in_the_master_plan_order() -> None:
    items = json.loads((REPO / "docs/forward-edge/fe-items.json").read_text(encoding="utf-8"))
    ids = [i["id"] for i in items]
    assert ids == [f"FE-{n:04d}" for n in range(1, 153)]
    assert set(tracker.entries()) == set(ids)


def test_every_cited_test_exists() -> None:
    known = _test_names()
    for fe_id, entry in tracker.entries().items():
        for name in entry.tests:
            assert name in known, f"{fe_id} cites missing test {name}"


def test_done_items_have_tests_and_not_started_items_claim_nothing() -> None:
    for fe_id, entry in tracker.entries().items():
        if entry.status == "DONE":
            assert entry.tests, f"{fe_id} is DONE without executable evidence"
        if entry.status == "NOT_STARTED":
            assert not entry.tests, f"{fe_id} is NOT_STARTED but cites tests"


def test_blocked_items_name_their_blocker() -> None:
    for fe_id, entry in tracker.entries().items():
        if entry.status == "BLOCKED":
            assert entry.blockers, f"{fe_id} is BLOCKED without a named blocker"


def test_external_providers_are_never_marked_done() -> None:
    entries = tracker.entries()
    for fe_id in ("FE-0035", "FE-0047", "FE-0133", "FE-0134", "FE-0135", "FE-0136", "FE-0138", "FE-0139"):
        assert entries[fe_id].status != "DONE", fe_id


def test_committed_tracker_matches_the_generator() -> None:
    assert (REPO / "docs/forward-edge/TRACKER.md").read_text(encoding="utf-8") == tracker.render()
    assert (REPO / "docs/forward-edge/BLOCKERS.md").read_text(encoding="utf-8") == tracker.render_blockers()
