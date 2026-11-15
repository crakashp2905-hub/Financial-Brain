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

ENGINE_ROOTS = (
    "src/financial_brain/paper/intraday_runner.py",
    "src/financial_brain/forward_edge/pipeline.py",
    "src/financial_brain/forward_edge/session_runner.py",
    "src/financial_brain/ingest/kite_live.py",
)


def _local_imports(tree: ast.AST, relative: str) -> set[str]:
    """Return resolvable in-repository module paths imported by one Python file."""
    rel = Path(relative).relative_to("src/financial_brain")
    parts = ["financial_brain", *rel.with_suffix("").parts]
    if parts[-1] == "__init__":
        parts.pop()
    package = parts[:-1]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("financial_brain."):
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[:len(package) - node.level + 1]
                module = ".".join([*base, *(node.module.split(".") if node.module else [])])
            else:
                module = node.module or ""
            if module == "financial_brain" or module.startswith("financial_brain."):
                found.add(module)
                found.update(f"{module}.{alias.name}" for alias in node.names if alias.name != "*")
    return found


def build(root: Path) -> dict:
    root = root.resolve()
    source_root = root / "src" / "financial_brain"
    matrix = json.loads((root / "docs/audits/capability-matrix.v1.json").read_text(encoding="utf-8"))
    curated: dict[str, list[dict]] = {}
    for capability in matrix["capabilities"]:
        for source in capability["source"]:
            curated.setdefault(source, []).append(capability)
    paths = sorted(source_root.rglob("*.py"))
    module_paths: dict[str, str] = {}
    for path in paths:
        rel = path.relative_to(root).as_posix()
        module = "financial_brain." + path.relative_to(source_root).with_suffix("").as_posix().replace("/", ".")
        if module.endswith(".__init__"):
            module = module.removesuffix(".__init__")
        module_paths[module] = rel
    rows = []
    edges: set[tuple[str, str]] = set()
    for path in paths:
        relative = path.relative_to(root).as_posix()
        package = path.relative_to(source_root).parts[0]
        evidence = curated.get(relative, [])
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            imports = sorted({node.module for node in ast.walk(tree)
                              if isinstance(node, ast.ImportFrom) and node.module})
            for imported in _local_imports(tree, relative):
                target = module_paths.get(imported)
                if target and target != relative:
                    edges.add((relative, target))
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
            "engine_graph_roots": list(ENGINE_ROOTS),
            "local_import_edges": [{"from": source, "to": target} for source, target in sorted(edges)],
            "sources": rows}


def canonical_bytes(inventory: dict) -> bytes:
    return json.dumps(inventory, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="print the complete inventory")
    parser.add_argument("--write", help="write the canonical inventory JSON to this repository-relative path")
    parser.add_argument("--check", help="fail if this repository-relative inventory is stale")
    args = parser.parse_args()
    report = build(Path(__file__).resolve().parents[1])
    encoded = canonical_bytes(report)
    if args.write:
        destination = Path(args.write)
        if not destination.is_absolute():
            destination = Path(__file__).resolve().parents[1] / destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(encoded + b"\n")
    if args.check:
        expected = Path(args.check)
        if not expected.is_absolute():
            expected = Path(__file__).resolve().parents[1] / expected
        if not expected.is_file() or expected.read_bytes().rstrip(b"\r\n") != encoded:
            raise SystemExit(f"source inventory is stale; run python scripts/source_inventory.py --write {args.check}")
    if args.json:
        print(encoded.decode("utf-8"))
    else:
        print(f"{report['source_count']} Python files; {report['unassessed_count']} unassessed; "
              f"{report['unassigned_role_count']} role-unassigned; sha256={hashlib.sha256(encoded).hexdigest()}")
    if any(row["parse_status"] != "ok" for row in report["sources"]):
        raise SystemExit("source parsing requires review")
