"""Fail-closed readiness audit for the research-to-learning promotion path.

This is a repository evidence gate, not a profitability certificate. It refuses to
report ready while dependent changes are unmerged, checklist items lack verified
completion evidence, or a market-data source lacks documented use and retention rights.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True)
class AuditCheck:
    name: str
    passed: bool
    blockers: tuple[str, ...]


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return value


def _freshness(document: dict, field: str, *, now: datetime, source_name: str) -> str | None:
    raw = document.get(field)
    if not isinstance(raw, str):
        return f"{source_name} has no {field} timestamp"
    try:
        observed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return f"{source_name} has an invalid {field} timestamp"
    if observed.tzinfo is None:
        return f"{source_name} {field} timestamp is not timezone-aware"
    age = now.astimezone(timezone.utc) - observed.astimezone(timezone.utc)
    if age < timedelta(0):
        return f"{source_name} {field} timestamp is in the future"
    if age > timedelta(hours=24):
        return f"{source_name} is stale ({age.total_seconds() / 3600:.1f} hours old; max 24)"
    return None


def audit(repo: Path, *, now: datetime | None = None) -> dict:
    """Audit merge dependencies, completion/test evidence, and source permissions.

    Missing or malformed evidence is a blocker. Inputs are read-only repository
    artifacts so CI and a local workstation produce the same result for one checkout.
    """
    repo = Path(repo).resolve()
    now = now or datetime.now(timezone.utc)
    errors: list[str] = []
    try:
        graph = _read_json(repo / "docs/edge-program/v3.0-pr-graph-2026-11-21.json")
        stale = _freshness(graph, "observed_at_utc", now=now, source_name="PR graph")
        if stale:
            errors.append(stale)
        prs = graph.get("pull_requests")
        if not isinstance(prs, list):
            raise ValueError("PR graph has no pull_requests list")
        by_number = {int(pr["number"]): pr for pr in prs}
        for pr in prs:
            number = pr.get("number", "?")
            state = str(pr.get("state", "unknown")).lower()
            if state != "merged":
                errors.append(f"PR #{number} is {state}, not merged")
            for dep in pr.get("depends_on", []):
                if dep not in by_number or str(by_number[dep].get("state", "unknown")).lower() != "merged":
                    errors.append(f"PR #{number} depends on unmerged or missing PR #{dep}")
    except (ValueError, TypeError, KeyError) as exc:
        errors.append(f"dependency evidence invalid: {exc}")
    dependency_check = AuditCheck("merged_dependencies", not errors, tuple(errors))

    errors = []
    try:
        status = _read_json(repo / "docs/edge-program/v3.0-status.json")
        summary = status.get("assessment_summary")
        if not isinstance(summary, dict):
            raise ValueError("checklist ledger has no assessment_summary")
        stale = _freshness(summary, "as_of_utc", now=now, source_name="checklist audit")
        if stale:
            errors.append(stale)
        items = status.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("checklist ledger has no items")
        for item in items:
            item_id = item.get("id", "unknown")
            if item.get("status") != "COMPLETE":
                errors.append(f"{item_id} is {item.get('status', 'unassessed')}")
                continue
            evidence = item.get("evidence") or []
            if not evidence:
                errors.append(f"{item_id} is COMPLETE without evidence")
                continue
            # COMPLETE claims must point to repository artifacts that still exist.
            for ref in evidence:
                candidate = str(ref).split(" (")[0].strip()
                if candidate.startswith(("src/", "tests/", "docs/", ".github/", "scripts/")):
                    if not (repo / candidate).exists():
                        errors.append(f"{item_id} references missing evidence {candidate}")
            tests = item.get("test_evidence") or []
            if not tests:
                errors.append(f"{item_id} is COMPLETE without test evidence")
            for ref in tests:
                candidate = str(ref).split(" (")[0].strip()
                if not (repo / candidate).exists():
                    errors.append(f"{item_id} references missing test evidence {candidate}")
        if int(status.get("total_items", -1)) != len(items):
            errors.append("checklist total_items does not match the item ledger")
    except (ValueError, TypeError, KeyError) as exc:
        errors.append(f"completion/test evidence invalid: {exc}")
    evidence_check = AuditCheck("checklist_and_test_evidence", not errors, tuple(errors))

    errors = []
    try:
        permissions = _read_json(repo / "docs/edge-program/market-data-sources.v1.json")
        stale = _freshness(permissions, "observed_at_utc", now=now, source_name="market-data permissions")
        if stale:
            errors.append(stale)
        sources = permissions.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ValueError("market-data register has no sources")
        for source in sources:
            source_id = source.get("id", "unknown")
            if source.get("status") != "READY":
                errors.append(f"{source_id} authorization status is {source.get('status', 'missing')}")
            retention = source.get("retention")
            if not isinstance(retention, dict) or retention.get("status") not in {"VERIFIED", "READY"}:
                errors.append(f"{source_id} retention permission is not verified")
    except (ValueError, TypeError, KeyError) as exc:
        errors.append(f"source-permission evidence invalid: {exc}")
    permission_check = AuditCheck("source_permissions", not errors, tuple(errors))

    checks = (dependency_check, evidence_check, permission_check)
    blockers = [f"{check.name}: {message}" for check in checks for message in check.blockers]
    return {
        "schema": "fb.learning_audit.v1",
        "status": "READY" if not blockers else "BLOCKED",
        "repo": str(repo),
        "checks": [asdict(check) for check in checks],
        "blocker_count": len(blockers),
        "blockers": blockers,
    }
