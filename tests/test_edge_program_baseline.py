import json
from pathlib import Path


def test_current_baseline_is_machine_readable_and_does_not_overclaim():
    baseline_path = Path(__file__).parents[1] / "docs" / "edge-program" / "current-baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))

    assert baseline["main"]["sha"] == "054193a524dd35eaa258df0ac352a58fc369e937"
    assert {item["number"] for item in baseline["main"]["recent_checklist_pull_requests"]} >= {
        27, 28, 30
    }
    assert baseline["branch_governance"]["main_classic_protection"] == "NOT_ENABLED"
    assert baseline["branch_governance"]["required_checks_enforced"] is False
    assert baseline["runtime"]["ci_operating_systems"] == ["ubuntu-latest", "windows-latest"]
    assert baseline["market_data"]["latest_observed_market_quote"] is None
    assert baseline["market_data"]["freshness_status"] == "NO_CURRENT_CAPTURE_EVIDENCED"
    assert baseline["paper_and_evidence_capabilities"]["independently_evidenced_live_sessions"] == 0
    assert baseline["paper_and_evidence_capabilities"]["profitability_claim"].startswith("NONE")
