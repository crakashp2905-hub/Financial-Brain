import json
from pathlib import Path


def test_current_baseline_is_machine_readable_and_does_not_overclaim():
    baseline_path = Path(__file__).parents[1] / "docs" / "edge-program" / "current-baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))

    assert baseline["main"]["sha"] == "91707001ceb87b7fe6dfb55369d04d9d2f1d7ed1"
    assert {item["number"] for item in baseline["main"]["recent_checklist_pull_requests"]} >= {
        27, 28, 30, 31
    }
    assert baseline["branch_governance"]["main_classic_protection"] == "ENABLED"
    assert baseline["branch_governance"]["required_checks_enforced"] is True
    assert baseline["branch_governance"]["required_approving_reviews"] == 1
    assert baseline["branch_governance"]["enforce_admins"] is True
    assert baseline["runtime"]["ci_operating_systems"] == ["ubuntu-latest", "windows-latest"]
    assert baseline["market_data"]["latest_observed_market_quote"] is None
    assert baseline["market_data"]["freshness_status"] == "NO_CURRENT_CAPTURE_EVIDENCED"
    assert baseline["paper_and_evidence_capabilities"]["independently_evidenced_live_sessions"] == 0
    assert baseline["paper_and_evidence_capabilities"]["profitability_claim"].startswith("NONE")


def test_branch_protection_evidence_matches_baseline_contract():
    root = Path(__file__).parents[1]
    evidence = json.loads((root / "docs" / "edge-program" / "main-branch-protection-evidence.json")
                          .read_text(encoding="utf-8"))
    assert evidence["classic_protection_enabled"] is True
    assert evidence["required_status_checks"] == {
        "strict": True,
        "contexts": ["test (ubuntu-latest)", "test (windows-latest)"],
    }
    assert evidence["required_pull_request_reviews"]["required_approving_review_count"] == 1
    assert evidence["required_pull_request_reviews"]["require_last_push_approval"] is True
    assert evidence["enforce_admins"] is True
    assert evidence["allow_force_pushes"] is False
    assert evidence["allow_deletions"] is False
