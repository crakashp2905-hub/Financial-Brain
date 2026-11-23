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
try:
    from scripts.text_hash import sha256_lf_normalized
except ModuleNotFoundError:  # direct `python scripts/source_inventory.py` invocation
    from text_hash import sha256_lf_normalized


ROLE_BY_PACKAGE = {
    "evaluation": "Research", "research": "Research", "features": "Research",
    "forecasting": "Research", "evidence": "Evidence", "pit": "Evidence",
    "paper": "Paper", "execution": "Execution", "risk": "Execution",
    "providers": "Integration", "ingest": "Integration", "interfaces": "UI",
}


def build(root: Path) -> dict:
    root = root.resolve()
    source_root = root / "src" / "financial_brain"
    matrix = json.loads((root / "docs/audits/capability-matrix.v2.json").read_text(encoding="utf-8"))
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
            source_text = path.read_text(encoding="utf-8")
            tree = ast.parse(source_text, filename=relative)
            imports = sorted({
                name
                for node in ast.walk(tree)
                for name in (
                    [node.module] if isinstance(node, ast.ImportFrom) and node.module
                    else [alias.name for alias in node.names] if isinstance(node, ast.Import)
                    else []
                )
            })
            parse_status = "ok"
        except (OSError, UnicodeError, SyntaxError):
            source_text, imports, parse_status = "", [], "needs_review"
        tests = sorted({test for item in evidence for test in item["tests"]})
        rows.append({
            "source": relative,
            "owner_role_hint": ROLE_BY_PACKAGE.get(package, "Unassigned"),
            "human_owner": None,
            "capability_ids": sorted(item["id"] for item in evidence),
            "test_paths": tests,
            "maturity": "locally_mapped" if evidence else "unassessed",
            "imports": imports,
            "source_sha256": sha256_lf_normalized(path.read_bytes()),
            "parse_status": parse_status,
        })
    return {"schema": "fb.source-inventory.v2", "scope": "Python source only; role hints are not owner assignments",
            "source_count": len(rows), "unassessed_count": sum(row["maturity"] == "unassessed" for row in rows),
            "unassigned_role_count": sum(row["owner_role_hint"] == "Unassigned" for row in rows),
            "sources": rows}


def canonical_bytes(inventory: dict) -> bytes:
    return json.dumps(inventory, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def rendered_bytes(inventory: dict) -> bytes:
    return (json.dumps(inventory, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="print the complete inventory")
    parser.add_argument("--output", type=Path, help="write the full inventory to this path")
    parser.add_argument("--check", action="store_true", help="fail if --output differs from the current source tree")
    args = parser.parse_args()
    report = build(Path(__file__).resolve().parents[1])
    encoded = canonical_bytes(report)
    if args.output:
        rendered = rendered_bytes(report)
        if args.check:
            current = args.output.read_bytes().replace(b"\r\n", b"\n") if args.output.is_file() else None
            if current != rendered:
                raise SystemExit(f"source inventory is stale: {args.output}")
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(rendered)
    if args.json:
        print(encoded.decode("utf-8"))
    else:
        print(f"{report['source_count']} Python files; {report['unassessed_count']} unassessed; "
              f"{report['unassigned_role_count']} role-unassigned; sha256={hashlib.sha256(encoded).hexdigest()}")
    if args.check and not args.output:
        raise SystemExit("--check requires --output")
    if any(row["parse_status"] != "ok" for row in report["sources"]):
        raise SystemExit("source parsing requires review")
