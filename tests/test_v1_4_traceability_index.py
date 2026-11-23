from __future__ import annotations

import json

from scripts.validate_v1_4_traceability_index import INDEX, STATUS, validate


def _inputs():
    return (
        json.loads(INDEX.read_text(encoding="utf-8")),
        json.loads(STATUS.read_text(encoding="utf-8")),
    )


def test_index_covers_all_v1_4_fb_tasks_with_source_and_test_fields():
    index, status = _inputs()

    assert len(index["tasks"]) == 405
    assert validate(index, status) == []


def test_index_rejects_missing_full_hash_for_committed_evidence():
    index, status = _inputs()
    record = index["tasks"][0]["source"][0]
    if record["binding_status"] == "UNCOMMITTED_WORKTREE_PREFIX_ONLY":
        record = next(
            evidence
            for task in index["tasks"]
            for evidence in task["source"]
            if evidence["binding_status"] == "VERIFIED"
        )
    record["object_sha"] = "short"

    assert any("lacks full commit/object SHA" in error for error in validate(index, status))


def test_index_keeps_uncommitted_worktree_sha_unbound():
    index, status = _inputs()
    record = next(
        evidence
        for task in index["tasks"]
        for evidence in task["source"]
        if evidence["binding_status"] == "UNCOMMITTED_WORKTREE_PREFIX_ONLY"
    )
    record["commit_sha"] = "a" * 40

    assert any("must not claim a commit SHA" in error for error in validate(index, status))
