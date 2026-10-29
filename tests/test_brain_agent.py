"""The coordinator can only classify saved evidence, never trade or self-approve."""
from datetime import date

from financial_brain.agents import brain


def opportunity(**changes):
    row = {"ticker": "ABC", "instrument": "INE000000001", "timestamp": "2026-10-27",
           "evidence": {"status": "IN_SAMPLE", "ids": ["e-1"]},
           "chain": {"expected_net_alpha": 0.02, "portfolio_adjusted_alpha": 0.01},
           "expected_cost": 0.004, "position_size": {"shares": 10},
           "invalidation": {"stop": 90, "rules": ["close below stop"]},
           "liquidity": {"adv20_inr": 10000000},
           "forecast_distribution": {"p10": -0.05, "p50": 0.01, "p90": 0.07}}
    row.update(changes)
    return row


def test_unconfirmed_opportunity_is_only_research_watch():
    result = brain.decide(opportunity(), {"level": "OK"}, today=date(2026, 10, 27))
    assert result["verdict"] == "RESEARCH_WATCH"
    assert result["execution_allowed"] is False
    assert [s["agent"] for s in result["specialists"]] == [
        "data", "evidence", "risk", "economics", "adversary"]


def test_absent_kill_state_and_stale_data_fail_closed():
    assert brain.decide(opportunity(), {}, today=date(2026, 10, 27))["verdict"] == "ABSTAIN"
    assert brain.decide(opportunity(timestamp="2026-10-15"), {"level": "OK"},
                        today=date(2026, 10, 27))["verdict"] == "ABSTAIN"
    assert brain.decide(opportunity(), {"level": "HALT"},
                        today=date(2026, 10, 27))["verdict"] == "ABSTAIN"


def test_forward_label_is_review_not_buy_and_bad_economics_abstains_from_risk():
    row = opportunity(evidence={"status": "PAPER_FORWARD", "ids": ["e-2"]})
    assert brain.decide(row, {"level": "OK"}, today=date(2026, 10, 27))["verdict"] == "HUMAN_REVIEW"
    row["chain"]["portfolio_adjusted_alpha"] = -0.001
    assert brain.decide(row, {"level": "OK"}, today=date(2026, 10, 27))["verdict"] == "NO_NEW_RISK"


def test_missing_evidence_and_invalidation_fail_closed():
    row = opportunity(evidence={"status": "CONFIRMED", "ids": []}, invalidation={})
    result = brain.run([row, None], {"level": "OK"}, today=date(2026, 10, 27))
    assert result["by_verdict"]["ABSTAIN"] == 2
    assert all(not d["execution_allowed"] for d in result["decisions"])
