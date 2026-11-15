from __future__ import annotations

from financial_brain.forward_edge import cockpit


def test_provisional_research_rank_never_receives_forward_champion_badge():
    html = cockpit.research_vs_champions(
        provisional=[
            {"strategy_id": "lower", "ranking_score": 0.1, "horizon_sessions": 5, "sample": 20},
            {"strategy_id": "higher", "ranking_score": 0.9, "horizon_sessions": 5, "sample": 12},
        ],
        forward_champions=[],
    )
    assert html.index("higher") < html.index("lower")
    assert "PROVISIONAL — RESEARCH ONLY" in html
    assert "No row in this section is a tradable edge or champion" in html
    provisional_section = html.split("<h2>Verified executable forward champions</h2>")[0]
    assert "class='promotable'" not in provisional_section


def test_only_complete_verified_executable_receipt_coverage_gets_champion_label():
    html = cockpit.research_vs_champions(
        provisional=[],
        forward_champions=[
            {"strategy_id": "verified", "status": "VERIFIED", "executable_forward": True,
             "evidence_source": "OBSERVED_FORWARD", "custody_status": "VERIFIED",
             "scheduled_sessions": 60, "verified_sessions": 60, "receipt_sessions": 60,
             "evidence_sha256": "a" * 64},
            {"strategy_id": "missing-receipts", "status": "VERIFIED", "executable_forward": True,
             "evidence_source": "OBSERVED_FORWARD", "custody_status": "VERIFIED",
             "scheduled_sessions": 60, "verified_sessions": 60, "receipt_sessions": 59,
             "evidence_sha256": "b" * 64},
            {"strategy_id": "research-only", "status": "EXPLORATORY", "executable_forward": True,
             "evidence_source": "OBSERVED_FORWARD", "custody_status": "VERIFIED",
             "scheduled_sessions": 100, "verified_sessions": 100, "receipt_sessions": 100,
             "evidence_sha256": "c" * 64},
            {"strategy_id": "all-green-fixture", "status": "VERIFIED", "executable_forward": True,
             "evidence_source": "SYNTHETIC_FIXTURE", "custody_status": "LOCAL_ONLY",
             "scheduled_sessions": 60, "verified_sessions": 60, "receipt_sessions": 60,
             "evidence_sha256": "d" * 64},
        ],
    )
    assert "VERIFIED EXECUTABLE FORWARD CHAMPION" in html
    assert html.count("INSUFFICIENT EVIDENCE — NOT A VERIFIED EXECUTABLE CHAMPION") == 3
    assert "missing-receipts" in html and "research-only" in html and "all-green-fixture" in html


def test_empty_forward_champion_population_is_explicitly_insufficient():
    html = cockpit.research_vs_champions(provisional=[], forward_champions=[])
    assert "INSUFFICIENT EVIDENCE — no forward observations supplied" in html
