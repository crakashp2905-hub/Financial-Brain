from __future__ import annotations

from financial_brain.forward_edge import cockpit


def test_win_rate_and_positive_total_rupees_alone_never_become_proven():
    html = cockpit.profitability_evidence([{
        "strategy_id": "lucky", "claim": "profitable", "win_rate": "0.91", "total_net_rupees": "250000",
    }])
    assert "INSUFFICIENT / NON-POSITIVE EVIDENCE" in html
    assert "multiplicity count" in html and "confidence interval" in html
    assert "Win rate (descriptive)" in html and "Total net rupees (descriptive)" in html
    assert "proven from win rate or cumulative rupees" in html


def test_positive_adjusted_lower_bound_is_presented_with_required_context_but_not_as_guarantee():
    html = cockpit.profitability_evidence([{
        "strategy_id": "candidate", "claim": "costed matched performance",
        "ci_level_pct": 95, "ci_lower": "0.002", "ci_upper": "0.009",
        "net_alpha_lcb": "0.002", "multiplicity_count": 14,
        "familywise_adjusted_alpha": "0.00357", "familywise_adjusted": True,
        "matched_benchmark_id": "nifty-sector-matched-v1",
        "matched_benchmark_net_alpha": "0.001", "estimated_capacity_inr": "5000000",
        "win_rate": "0.58", "total_net_rupees": "25000",
    }])
    for required in ("CI level %", "CI lower", "CI upper", "Multiplicity count", "Adjusted alpha",
                     "Matched benchmark", "Estimated capacity INR", "0.58", "25000"):
        assert required in html
    assert "POSITIVE ADJUSTED LOWER BOUND — NOT A GUARANTEE" in html
    assert "PROVEN" not in html


def test_missing_or_invalid_interval_and_capacity_keep_claim_insufficient():
    html = cockpit.profitability_evidence([{
        "strategy_id": "bad", "ci_level_pct": 95, "ci_lower": "0.1", "ci_upper": "-0.1",
        "net_alpha_lcb": "0.1", "multiplicity_count": 2, "familywise_adjusted_alpha": "0.025",
        "familywise_adjusted": True, "matched_benchmark_id": "matched", "matched_benchmark_net_alpha": "0",
        "estimated_capacity_inr": "0",
    }])
    assert "INSUFFICIENT / NON-POSITIVE EVIDENCE" in html
    assert "valid confidence interval" in html and "positive estimated capacity" in html
