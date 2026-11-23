"""Inventory public module-level entry-point candidates across trading domains.

This is a static symbol inventory, not proof that all candidates share one runtime
contract. Exact owner IDs in authoritative-contracts.v1.json remain the reviewed map.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DOMAINS = ("forward_edge", "paper", "risk", "execution", "evaluation", "forecasting", "strategies", "portfolio")
CONTRACTS = ROOT / "docs/edge-program/authoritative-contracts.v1.json"
DEFAULT_OUTPUT = ROOT / "docs/edge-program/entrypoint-inventory.v1.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def build(root: Path = ROOT) -> dict[str, Any]:
    root = root.resolve()
    contracts_data = json.loads((root / "docs/edge-program/authoritative-contracts.v1.json").read_text(encoding="utf-8"))
    contracts = contracts_data["contracts"]
    rows: list[dict[str, Any]] = []
    module_counts: Counter[str] = Counter()
    symbol_counts: Counter[str] = Counter()
    modules: list[dict[str, str]] = []

    for domain in DOMAINS:
        package = root / "src/financial_brain" / domain
        for path in sorted(package.rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=relative)
            file_hash = _sha256(path)
            modules.append({"domain": domain, "path": relative, "source_sha256": file_hash})
            for node in tree.body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                if node.name.startswith("_"):
                    continue
                kind = "class" if isinstance(node, ast.ClassDef) else (
                    "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function")
                symbol_id = f"{relative}::{node.name}"
                canonical = [c["id"] for c in contracts if c["owner"] == symbol_id]
                supporting = [c["id"] for c in contracts if any(
                    ref == relative or ref == symbol_id
                    for ref in c.get("supporting", []))]
                alternatives = [c["id"] for c in contracts if any(
                    ref == relative or ref == symbol_id
                    for ref in c.get("alternatives", []))]
                role = "CANONICAL_OWNER" if canonical else (
                    "SUPPORTING" if supporting else "ALTERNATIVE" if alternatives else "UNREVIEWED")
                rows.append({
                    "id": symbol_id,
                    "domain": domain,
                    "kind": kind,
                    "line": node.lineno,
                    "source_sha256": file_hash,
                    "contract_ids": sorted(set(canonical + supporting + alternatives)),
                    "canonical_contract_ids": canonical,
                    "related_role": role,
                })
                module_counts[domain] += 1
                symbol_counts[node.name] += 1

    candidate_ids = {row["id"] for row in rows}
    owner_bindings = []
    unresolved_owners = []
    for contract in contracts:
        owner = contract["owner"]
        exact = "::" in owner and owner in candidate_ids
        binding = {"contract_id": contract["id"], "owner": owner, "exact_symbol": exact}
        owner_bindings.append(binding)
        if not exact:
            unresolved_owners.append(contract["id"])

    duplicate_names = [
        {"name": name, "count": count, "candidate_ids": [row["id"] for row in rows if row["id"].endswith(f"::{name}")]}
        for name, count in sorted(symbol_counts.items()) if count > 1
    ]
    return {
        "schema": "fb.entrypoint-inventory.v1",
        "scope": "Public top-level Python functions/classes only across the seven checklist domains plus portfolio accounting. Static candidates are not proof of shared semantics. Private names, methods, dynamically registered callables and external schedulers are excluded.",
        "domains": list(DOMAINS),
        "domain_counts": {domain: module_counts[domain] for domain in DOMAINS},
        "module_count": len(modules),
        "candidate_count": len(rows),
        "canonical_contract_owner_count": sum(bool(row["canonical_contract_ids"]) for row in rows),
        "unreviewed_candidate_count": sum(row["related_role"] == "UNREVIEWED" for row in rows),
        "unresolved_contract_owner_ids": unresolved_owners,
        "contract_owner_bindings": owner_bindings,
        "duplicate_symbol_names": duplicate_names,
        "modules": modules,
        "candidates": rows,
        "authority_map": "docs/edge-program/authoritative-contracts.v1.json",
    }


def render(report: dict[str, Any]) -> bytes:
    return (json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    expected = render(build(ROOT))
    if args.check:
        if not output.is_file() or output.read_bytes().replace(b"\r\n", b"\n") != expected:
            raise SystemExit(f"entry-point inventory is stale: {output}")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(expected)
    report = json.loads(expected)
    print(f"entry-point inventory {'verified' if args.check else 'written'}: "
          f"{report['candidate_count']} candidates across {report['module_count']} modules; "
          f"{report['unreviewed_candidate_count']} candidates await contract review; "
          f"{len(report['unresolved_contract_owner_ids'])} owners lack exact symbol bindings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
