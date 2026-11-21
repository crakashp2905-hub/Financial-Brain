"""Check that every documented engineering domain routes through CODEOWNERS."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def validate() -> list[str]:
    data = json.loads((ROOT / "docs/edge-program/ownership-map.v1.json").read_text(encoding="utf-8"))
    codeowners = (ROOT / ".github/CODEOWNERS").read_text(encoding="utf-8")
    errors: list[str] = []
    domains = data.get("domains")
    if not isinstance(domains, list) or not domains:
        return ["ownership map must contain domains"]
    seen: set[str] = set()
    for domain in domains:
        domain_id = domain.get("id")
        if not isinstance(domain_id, str) or domain_id in seen:
            errors.append(f"invalid or duplicate domain id: {domain_id!r}")
        seen.add(domain_id)
        owner = domain.get("owner")
        paths = domain.get("paths")
        if not isinstance(owner, str) or not owner.startswith("@"):
            errors.append(f"{domain_id}: owner must be an explicit GitHub account or team")
            continue
        if not isinstance(paths, list) or not paths:
            errors.append(f"{domain_id}: at least one path is required")
            continue
        for path in paths:
            rule = next((line.split("#", 1)[0].split() for line in codeowners.splitlines()
                         if line.split("#", 1)[0].split()[:1] == [path]), None)
            if rule is None or owner not in rule[1:]:
                errors.append(f"{domain_id}: CODEOWNERS does not route {path} to {owner}")
    return errors


def main() -> int:
    errors = validate()
    if errors:
        raise SystemExit("\n".join(errors))
    print("CODEOWNERS covers all documented ownership domains")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
