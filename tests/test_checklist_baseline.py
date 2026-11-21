from scripts.audit_checklist_baseline import validate


def test_frozen_checklist_baseline_matches_its_commit_tree_and_lock():
    assert validate() == []
