"""Check the source fingerprints and internal arithmetic in the search-space audit."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs/edge-program/research-search-space-reconciliation.v1.json"


def validate(report: dict[str, Any], root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    if report.get("schema") != "fb.research_search_space_reconciliation.v1":
        return ["unsupported research search-space report schema"]
    for source in report.get("sources", []):
        path = root / source["path"]
        try:
            content = path.read_bytes().replace(b"\r\n", b"\n")
        except OSError:
            errors.append(f"missing source: {source['path']}")
            continue
        if hashlib.sha256(content).hexdigest() != source.get("sha256_lf"):
            errors.append(f"source fingerprint changed: {source['path']}")
    family_counts = report.get("documented_historical_family_counts", {})
    if sum(family_counts.values()) != 150:
        errors.append("the documented 150-trial family table must sum to 150")
    claims = report.get("claims", {})
    if claims.get("historical_total_counts") != [150, 151]:
        errors.append("both contradictory historical totals must remain visible")
    if claims.get("q4_2026_protected_trials") != 13:
        errors.append("the protected Q4 trial count must remain 13")
    if claims.get("historical_total_reconciled") is not False:
        errors.append("unavailable historical aggregate must not be marked reconciled")
    return errors


def main() -> int:
    try:
        report = json.loads(REPORT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL cannot read research search-space report: {exc}")
        return 1
    errors = validate(report)
    if errors:
        for error in errors:
            print(f"FAIL {error}")
        return 1
    print("research search-space evidence valid; historical 150/151 look total remains explicitly unreconciled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
