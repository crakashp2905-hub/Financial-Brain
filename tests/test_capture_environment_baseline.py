from __future__ import annotations

from scripts.capture_environment_baseline import compare_capture


def _baseline() -> dict:
    return {
        "repository": {"commit_sha": "a" * 40, "working_tree_clean": True},
        "runtime": {"python": "3.13", "os": "Windows"},
        "dependencies": {"lock_sha256": "b" * 64},
        "datasource_entitlements": {"sources": [{"source_id": "kite", "status": "UNVERIFIED"}]},
        "scheduled_tasks": {"complete_checkout_paths_verified": True},
    }


def test_capture_comparison_accepts_exact_recorded_environment():
    captured = _baseline()

    assert compare_capture(captured, _baseline()) == ([], True)


def test_capture_comparison_rejects_unrecorded_commit_change():
    captured = _baseline()
    expected = _baseline()
    captured["repository"]["commit_sha"] = "c" * 40

    assert compare_capture(captured, expected) == (["repository"], True)


def test_capture_comparison_fails_closed_when_scheduler_paths_are_unknown():
    captured = _baseline()
    captured["scheduled_tasks"]["complete_checkout_paths_verified"] = False

    assert compare_capture(captured, captured) == ([], False)
