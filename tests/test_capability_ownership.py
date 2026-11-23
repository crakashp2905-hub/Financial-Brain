from pathlib import Path

from scripts.validate_capability_ownership import REQUIRED_DOMAINS, validate


def test_capability_ownership_map_covers_required_domains_and_existing_paths() -> None:
    root = Path(__file__).resolve().parents[1]
    assert validate(root) == []


def test_capability_ownership_map_does_not_claim_unverified_github_owners() -> None:
    import json

    root = Path(__file__).resolve().parents[1]
    document = json.loads(
        (root / "docs/edge-program/capability-ownership.v1.json").read_text(encoding="utf-8")
    )
    assert document["github_codeowners_ready"] is False
    assert {domain["id"] for domain in document["domains"]} == REQUIRED_DOMAINS
    assert all(domain["human_owner"] is None for domain in document["domains"])
