import json

from scripts.audit_checklist_baseline import LATEST_BASELINE, validate, validate_latest_snapshot


def test_frozen_checklist_baseline_matches_its_commit_tree_and_lock():
    assert validate() == []


def test_latest_baseline_records_unknown_scheduler_without_claiming_verification():
    snapshot = json.loads(LATEST_BASELINE.read_text(encoding="utf-8"))
    assert validate_latest_snapshot(snapshot) == []
    snapshot["active_scheduled_checkout"]["status"] = "VERIFIED"
    assert "verified scheduled checkout requires its deployed commit SHA" in validate_latest_snapshot(snapshot)


def test_latest_baseline_requires_ci_for_each_os_and_reproducibility_job():
    snapshot = json.loads(LATEST_BASELINE.read_text(encoding="utf-8"))
    snapshot["ci"]["jobs_at_capture"].pop("test (windows-latest)")
    assert "latest baseline does not capture all required CI jobs" in validate_latest_snapshot(snapshot)
