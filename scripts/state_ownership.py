"""Validate scoped authoritative state ownership without conflating separate books."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def validate(document: dict[str, Any], root: Path) -> None:
    if document.get("schema") != "fb.edge-state-ownership.v1":
        raise ValueError("unsupported state ownership schema")
    seen: set[tuple[str, str]] = set()
    for authority in document.get("authorities", []):
        scope = str(authority.get("scope_id", "")).strip()
        domain = str(authority.get("domain", "")).strip()
        owner = str(authority.get("owner", "")).strip()
        if not scope or not domain or not owner:
            raise ValueError("authorities require scope_id, domain and owner")
        key = (scope, domain)
        if key in seen:
            raise ValueError(f"duplicate authoritative owner for {scope}/{domain}")
        seen.add(key)
        if not (root / owner).is_file():
            raise ValueError(f"owner source does not exist: {owner}")
        if authority.get("authority_kind") not in {
            "durable_event_source", "hash_chained_local_journal", "paper_book_store"
        }:
            raise ValueError(f"unknown authority kind for {scope}/{domain}")
    unresolved_seen: set[tuple[str, str]] = set()
    for unresolved in document.get("unresolved_domains", []):
        key = (str(unresolved.get("scope_id", "")), str(unresolved.get("domain", "")))
        if unresolved.get("status") != "not_established":
            raise ValueError("unresolved domains must stay explicitly not_established")
        if key in seen:
            raise ValueError("a domain cannot be both authoritative and unresolved in the same scope")
        if key in unresolved_seen:
            raise ValueError(f"duplicate unresolved domain declaration: {key[0]}/{key[1]}")
        unresolved_seen.add(key)
        if not (root / str(unresolved.get("current_artifact", ""))).is_file():
            raise ValueError(f"unresolved domain artifact does not exist: {unresolved.get('current_artifact')}")


def load(root: Path) -> dict[str, Any]:
    path = root / "docs/edge-program/state-ownership.v1.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    validate(document, root)
    return document


if __name__ == "__main__":
    repository = Path(__file__).resolve().parents[1]
    report = load(repository)
    print(f"validated {len(report['authorities'])} scoped state owners; "
          f"{len(report['unresolved_domains'])} domains remain explicitly unowned")
