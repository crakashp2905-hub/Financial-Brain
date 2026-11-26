from pathlib import Path

from scripts.audit.claim_safety import scan, violations

ROOT = Path(__file__).resolve().parents[1]


def test_unqualified_performance_claim_families_are_rejected() -> None:
    examples = (
        "Guaranteed profits are assured.",
        "This is a zero-loss strategy.",
        "High accuracy proves alpha.",
        "Synthetic forward results establish a durable edge.",
        "Realized return from a fallback close is valid P&L.",
    )
    for example in examples:
        assert violations(example), example


def test_explicit_prohibitions_and_caveats_are_allowed() -> None:
    examples = (
        "No strategy has guaranteed profits.",
        "Never promise zero-loss trading.",
        "Accuracy is not alpha.",
        "Do not present synthetic forward results as observed performance.",
        "Never manufacture realized return from a fallback close.",
    )
    for example in examples:
        assert not violations(example), example


def test_repository_prose_has_no_unqualified_prohibited_claims() -> None:
    assert scan(ROOT) == []
