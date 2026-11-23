"""Validate the repository capability ownership map's paths and domain coverage."""
from __future__ import annotations

import json
from pathlib import Path

REQUIRED_DOMAINS = {
    "source_adapters", "simulation", "reinforcement_learning", "research_governance",
    "risk", "portfolio", "trader_ux", "accounting",
}


def validate(root: Path) -> list[str]:
    document = json.loads(
        (root / "docs/edge-program/capability-ownership.v1.json").read_text(encoding="utf-8")
    )
    errors: list[str] = []
    if document.get("schema") != "fb.capability-ownership.v1":
        errors.append("unsupported capability ownership schema")
    domains = document.get("domains", [])
    ids = [domain.get("id") for domain in domains]
    missing = REQUIRED_DOMAINS - set(ids)
    if missing:
        errors.append(f"missing required domains: {', '.join(sorted(missing))}")
    if len(ids) != len(set(ids)):
        errors.append("duplicate domain IDs")
    for domain in domains:
        if not domain.get("accountable_role") or not domain.get("responsibility"):
            errors.append(f"domain lacks role or responsibility: {domain.get('id')}")
        paths = domain.get("paths", [])
        if not paths:
            errors.append(f"domain has no path coverage: {domain.get('id')}")
        for path_text in paths:
            path = root / path_text.removesuffix("/")
            if not path.exists():
                errors.append(f"mapped path does not exist: {path_text}")
        if domain.get("human_owner") is not None and not str(domain["human_owner"]).strip():
            errors.append(f"invalid human owner: {domain.get('id')}")
    if document.get("github_codeowners_ready") and any(d.get("human_owner") is None for d in domains):
        errors.append("GitHub CODEOWNERS cannot be ready while human owners are unset")
    return errors


if __name__ == "__main__":
    repository = Path(__file__).resolve().parents[1]
    problems = validate(repository)
    if problems:
        raise SystemExit("\n".join(problems))
    print(f"validated {len(REQUIRED_DOMAINS)} capability ownership domains")
