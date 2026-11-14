"""Generate a deterministic source-linked catalogue of existing strategy variants.

This is an inventory aid, not a registry approval or strategy-performance claim.
Variants without declared asset/clock metadata retain explicit UNSPECIFIED values.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = Path("docs/edge-program/strategy-variant-inventory.json")
CATEGORIES = ("historical_trial", "paper_zoo", "technical_search", "chart_pattern",
              "options", "futures", "ranking_portfolio", "python_strategy_module")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _normalized_source_bytes(path: Path) -> bytes:
    """Return checkout-independent source bytes (Git stores text files with LF endings)."""
    return path.read_bytes().replace(b"\r\n", b"\n")


def _path_hash(path: Path) -> str:
    return _sha(_normalized_source_bytes(path))


def _literal_assignment(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            if node.value is None:
                continue
            targets, value = [node.target], node.value
        else:
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            return ast.literal_eval(value)
    raise ValueError(f"missing literal {name} in {path}")


def _literal_dict_string_keys(path: Path, name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            if node.value is None:
                continue
            targets, value = [node.target], node.value
        else:
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in targets):
            continue
        if not isinstance(value, ast.Dict):
            return set()
        return {key.value for key in value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)}
    raise ValueError(f"missing dictionary {name} in {path}")


def _add(rows: list[dict], *, category: str, family: str, name: str, source: Path,
         asset: str = "UNSPECIFIED", clock: str = "UNSPECIFIED", version: int = 1,
         status: str = "SHADOW_ONLY", granularity: str = "EXPLICIT_VARIANT",
         parameters: dict | None = None) -> None:
    source_hash = _path_hash(source)
    variant_body = json.dumps(parameters or {}, sort_keys=True, separators=(",", ":"), allow_nan=False)
    variant_hash = _sha(_normalized_source_bytes(source) + b"\0" + variant_body.encode("utf-8"))
    rows.append({
        "family": family,
        "name": name,
        "version": version,
        "asset": asset,
        "clock": clock,
        "category": category,
        "status": status,
        "catalogue_granularity": granularity,
        "source": source.relative_to(ROOT).as_posix(),
        "source_sha256": source_hash,
        "variant_sha256": variant_hash,
        "parameters": parameters or {},
    })


def build(root: Path = ROOT) -> dict:
    global ROOT
    ROOT = Path(root).resolve()
    rows: list[dict] = []
    ledger = ROOT / "docs/trials/ledger.jsonl"
    for line_number, line in enumerate(ledger.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("kind") != "trial":
            continue
        row_path = ledger
        family = str(entry.get("name", "historical_trial")).split(maxsplit=1)[0].lower()
        raw_hash = _sha((line + f"\nline:{line_number}").encode("utf-8"))
        rows.append({"family": f"historical:{family}", "name": str(entry["name"]), "version": 1,
                     "asset": "UNSPECIFIED", "clock": "UNSPECIFIED", "category": "historical_trial",
                     "status": str(entry.get("verdict", "UNSPECIFIED")),
                     "catalogue_granularity": "RECORDED_TRIAL", "source": row_path.relative_to(ROOT).as_posix(),
                     "source_sha256": _path_hash(row_path), "variant_sha256": raw_hash,
                     "parameters": {"verdict": entry.get("verdict"), "failed_gates": entry.get("failed", [])}})

    zoo = ROOT / "src/financial_brain/paper/zoo.py"
    for name in _literal_assignment(zoo, "RULES"):
        _add(rows, category="paper_zoo", family="daily_technical", name=name, source=zoo,
             asset="EQUITY", clock="UNSPECIFIED", granularity="EXPLICIT_ZOO_RULE")
    zoo_text = zoo.read_text(encoding="utf-8")
    zoo_names = _literal_dict_string_keys(zoo, "KIND")
    zoo_names.update(re.findall(r"['\"](edge_[A-Za-z0-9_]+)['\"]", zoo_text))
    zoo_names.update({"pairs_distance", "pairs_coint", "pca_statarb", "vix_mean_reversion",
                      "scale_trader", "grid", "martingale", "anti_martingale", "carry_basis_10",
                      "sharpe_allocator", "results_reaction_short"})
    for name in sorted(zoo_names):
        if name not in set(_literal_assignment(zoo, "RULES")):
            _add(rows, category="paper_zoo", family="paper_zoo", name=name, source=zoo,
                 asset="UNSPECIFIED", clock="UNSPECIFIED", granularity="EXPLICIT_ZOO_ID")

    sys.path.insert(0, str(ROOT / "src"))
    try:
        from financial_brain.evaluation.bounded_search import SEED, variants
        for variant in variants(seed=SEED, random_budget=48):
            params = {key: value for key, value in vars(variant).items() if key != "name"}
            _add(rows, category="technical_search", family=variant.family, name=variant.name,
                 source=ROOT / "src/financial_brain/evaluation/bounded_search.py",
                 asset="EQUITY", clock="DAILY_CLOSE", parameters=params)
    finally:
        sys.path.remove(str(ROOT / "src"))

    pattern_source = ROOT / "docs/sources/pattern-catalogue.v1.json"
    catalogue = json.loads(pattern_source.read_text(encoding="utf-8"))
    for pattern in catalogue.get("patterns", []):
        _add(rows, category="chart_pattern", family=str(pattern.get("family", "pattern")),
             name=str(pattern.get("id", pattern.get("name", "unknown"))), source=pattern_source,
             granularity="SOURCE_CATALOGUE_DEFINITION", parameters={"pattern_name": pattern.get("name")})

    module_groups = {
        "options": [ROOT / "src/financial_brain/paper/options.py", ROOT / "src/financial_brain/paper/options_runner.py",
                    ROOT / "src/financial_brain/paper/options_forward.py", ROOT / "src/financial_brain/paper/options_spread.py",
                    ROOT / "src/financial_brain/evaluation/options_real.py",
                    ROOT / "src/financial_brain/evaluation/options_vertical.py",
                    ROOT / "src/financial_brain/evaluation/options_synth.py"],
        "futures": [ROOT / "src/financial_brain/forward_edge/futures.py",
                    ROOT / "src/financial_brain/kernel/futures_paper.py",
                    ROOT / "src/financial_brain/kernel/futures_analytics.py",
                    ROOT / "src/financial_brain/evaluation/edges.py"],
        "ranking_portfolio": [ROOT / "src/financial_brain/evaluation/momentum_book.py",
                              ROOT / "src/financial_brain/portfolio/optimizer.py",
                              ROOT / "src/financial_brain/paper/zoo.py",
                              ROOT / "src/financial_brain/paper/zoo_edges.py"],
    }
    for category, sources in module_groups.items():
        for source in sources:
            if source.is_file():
                _add(rows, category=category, family=f"module:{source.stem}", name=source.stem,
                     source=source, granularity="MODULE_PROXY")
    strategy_dir = ROOT / "src/financial_brain/strategies"
    for source in sorted(strategy_dir.glob("*.py")):
        if source.name != "__init__.py":
            _add(rows, category="python_strategy_module", family="python_strategy_module",
                 name=source.stem, source=source, granularity="MODULE_PROXY")

    rows.sort(key=lambda row: (row["family"], row["name"], row["version"], row["asset"], row["clock"],
                               row["source"], row["variant_sha256"]))
    keys = [(row["family"], row["name"], row["version"], row["asset"], row["clock"]) for row in rows]
    if len(set(keys)) != len(keys):
        raise ValueError("strategy inventory contains a duplicate (family, name, version, asset, clock) key")
    counts = {category: sum(row["category"] == category for row in rows) for category in CATEGORIES}
    return {"schema_version": "1.0", "inventory_status": "PARTIAL_SOURCE_LINKED_CANDIDATE_INVENTORY",
            "unique_key": ["family", "name", "version", "asset", "clock"],
            "candidate_count": len(rows), "category_counts": counts,
            "unknown_asset_or_clock_count": sum(row["asset"] == "UNSPECIFIED" or row["clock"] == "UNSPECIFIED"
                                                 for row in rows),
            "limitations": [
                "Module proxies and source-catalogue definitions are not necessarily executable strategy variants.",
                "Options/futures/ranking module proxies need adapter-level variant enumeration and explicit asset/clock metadata.",
                "The inventory is not a trial approval, eligibility grant, or performance result.",
            ], "items": rows}


def render(root: Path = ROOT) -> str:
    return json.dumps(build(root), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    path = ROOT / args.output
    expected = render(ROOT)
    if args.write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(expected, encoding="utf-8", newline="\n")
    if args.check:
        if not path.exists() or path.read_text(encoding="utf-8") != expected:
            print(f"strategy inventory stale; run python scripts/strategy_variant_inventory.py --write {args.output}")
            return 1
    summary = json.loads(expected)
    print(f"{summary['candidate_count']} source-linked candidates; {summary['unknown_asset_or_clock_count']} need asset/clock qualification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
