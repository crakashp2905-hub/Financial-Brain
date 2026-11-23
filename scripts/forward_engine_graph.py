"""Build and verify import closures for the legacy and canonical paper runners."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
try:
    from scripts.text_hash import sha256_lf_normalized
except ModuleNotFoundError:  # direct `python scripts/forward_engine_graph.py` invocation
    from text_hash import sha256_lf_normalized


ENTRYPOINTS = {
    "legacy_intraday_paper": "financial_brain.paper.intraday_runner",
    "forward_session_runner": "financial_brain.forward_edge.session_runner",
}


def _module_for(path: Path, source_root: Path) -> str:
    parts = list(path.relative_to(source_root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imports(path: Path, module: str, modules: set[str]) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
    package_parts = module.split(".") if path.name == "__init__.py" else module.split(".")[:-1]
    found: set[str] = set()
    for node in ast.walk(tree):
        candidates: list[str] = []
        if isinstance(node, ast.Import):
            candidates.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base_parts = package_parts[:len(package_parts) - node.level + 1] if node.level else []
            if node.module:
                base_parts.extend(node.module.split("."))
            base = ".".join(base_parts)
            candidates.append(base)
            candidates.extend(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
        for candidate in candidates:
            bits = candidate.split(".")
            for end in range(len(bits), 0, -1):
                local = ".".join(bits[:end])
                if local in modules:
                    found.add(local)
                    break
    return found


def build(root: Path) -> dict:
    root = root.resolve()
    src_root = root / "src"
    package_root = src_root / "financial_brain"
    files = { _module_for(path, src_root): path for path in package_root.rglob("*.py") }
    modules = set(files)
    edges = {module: _imports(path, module, modules) for module, path in files.items()}
    closures: dict[str, set[str]] = {}
    for label, entry in ENTRYPOINTS.items():
        if entry not in modules:
            raise ValueError(f"missing graph entrypoint: {entry}")
        reached = {entry}
        pending = [entry]
        while pending:
            current = pending.pop()
            for dependency in edges[current] - reached:
                reached.add(dependency)
                pending.append(dependency)
        closures[label] = reached
    graph_edges = [
        {"source": source, "target": target}
        for source in sorted(set.union(*closures.values()))
        for target in sorted(edges[source])
        if target in set.union(*closures.values())
    ]
    source_hashes = {
        path.relative_to(root).as_posix(): sha256_lf_normalized(path.read_bytes())
        for path in sorted(package_root.rglob("*.py"))
    }
    shared = sorted(closures["legacy_intraday_paper"] & closures["forward_session_runner"])
    return {
        "schema": "fb.forward-engine-dependency-graph.v1",
        "scope": "Static Python import closure only; it does not prove runtime call order or data ownership.",
        "source_tree_sha256": hashlib.sha256(json.dumps(source_hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "entrypoints": ENTRYPOINTS,
        "closures": {key: sorted(value) for key, value in closures.items()},
        "shared_modules": shared,
        "edges": graph_edges,
        "source_count": len(source_hashes),
    }


def rendered_bytes(graph: dict) -> bytes:
    return (json.dumps(graph, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("docs/edge-program/forward-engine-dependency-graph.v1.json"))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    output = args.output if args.output.is_absolute() else repository / args.output
    rendered = rendered_bytes(build(repository))
    if args.check:
        current = output.read_bytes().replace(b"\r\n", b"\n") if output.is_file() else None
        if current != rendered:
            raise SystemExit(f"forward-engine dependency graph is stale: {output}")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(rendered)
    print(f"forward-engine dependency graph {'verified' if args.check else 'written'}: {output}")
