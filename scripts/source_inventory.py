"""Build a conservative source inventory without inventing human ownership or proof.

The committed capability matrix supplies reviewed source-to-test mappings for a
small critical subset. Every other module is explicitly unassessed. Role labels
are directory-based routing hints, not assigned people or production approval.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path


ROLE_BY_PACKAGE = {
    "evaluation": "Research", "research": "Research", "features": "Research",
    "forecasting": "Research", "evidence": "Evidence", "pit": "Evidence",
    "paper": "Paper", "execution": "Execution", "risk": "Execution",
    "providers": "Integration", "ingest": "Integration", "interfaces": "UI",
}


def build(root: Path) -> dict:
    root = root.resolve()
    source_root = root / "src" / "financial_brain"
    matrix = json.loads((root / "docs/audits/capability-matrix.v1.json").read_text(encoding="utf-8"))
    curated: dict[str, list[dict]] = {}
    for capability in matrix["capabilities"]:
        for source in capability["source"]:
            curated.setdefault(source, []).append(capability)
    rows = []
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        package = path.relative_to(source_root).parts[0]
        evidence = curated.get(relative, [])
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            imports = sorted({node.module for node in ast.walk(tree)
                              if isinstance(node, ast.ImportFrom) and node.module})
            parse_status = "ok"
        except (OSError, UnicodeError, SyntaxError):
            imports, parse_status = [], "needs_review"
        tests = sorted({test for item in evidence for test in item["tests"]})
        rows.append({
            "source": relative,
            "owner_role_hint": ROLE_BY_PACKAGE.get(package, "Unassigned"),
            "human_owner": None,
            "capability_ids": sorted(item["id"] for item in evidence),
            "test_paths": tests,
            "maturity": "locally_mapped" if evidence else "unassessed",
            "imports": imports,
            "parse_status": parse_status,
        })
    return {"schema": "fb.source-inventory.v1", "scope": "Python source only; role hints are not owner assignments",
            "source_count": len(rows), "unassessed_count": sum(row["maturity"] == "unassessed" for row in rows),
            "unassigned_role_count": sum(row["owner_role_hint"] == "Unassigned" for row in rows),
            "sources": rows}


def canonical_bytes(inventory: dict) -> bytes:
    return json.dumps(inventory, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="print the complete inventory")
    args = parser.parse_args()
    report = build(Path(__file__).resolve().parents[1])
    encoded = canonical_bytes(report)
    if args.json:
        print(encoded.decode("utf-8"))
    else:
        print(f"{report['source_count']} Python files; {report['unassessed_count']} unassessed; "
              f"{report['unassigned_role_count']} role-unassigned; sha256={hashlib.sha256(encoded).hexdigest()}")
    if any(row["parse_status"] != "ok" for row in report["sources"]):
        raise SystemExit("source parsing requires review")
