import json

from scripts.validate_pr_dependency_snapshot import (
    OPEN_PR_SNAPSHOT,
    SNAPSHOT,
    validate_open_pr_snapshot,
    validate_snapshot,
)


def test_current_pr_dependency_snapshot_matches_exact_changed_paths():
    graph = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert validate_snapshot(graph) == []


def test_stack_dependencies_follow_exact_base_branch_refs():
    graph = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert graph["nodes"]["36"]["depends_on"] == [37]
    assert graph["nodes"]["38"]["depends_on"] == [36]


def test_path_overlap_never_transfers_validation():
    graph = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    graph["overlaps"][0]["validation_transfer"] = "INHERITED"
    assert any("incorrectly implies inherited validation" in error
               for error in validate_snapshot(graph))


def test_all_open_pr_snapshot_records_current_ancestry_checks_and_protected_artifacts():
    snapshot = json.loads(OPEN_PR_SNAPSHOT.read_text(encoding="utf-8"))
    assert len(snapshot["pull_requests"]) == 13
    assert validate_open_pr_snapshot(snapshot) == []


def test_all_open_pr_snapshot_rejects_checks_for_a_different_head():
    snapshot = json.loads(OPEN_PR_SNAPSHOT.read_text(encoding="utf-8"))
    snapshot["pull_requests"][0]["check_runs"][0]["head_sha"] = "0" * 40

    assert any("differently pinned CI evidence" in error
               for error in validate_open_pr_snapshot(snapshot))
