"""Fail when a critical trading domain falls below its versioned coverage floor."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "docs/edge-program/critical-coverage-policy.json"


def _relative(path: str, root: Path) -> str:
    value = Path(path)
    if value.is_absolute():
        try:
            value = value.relative_to(root)
        except ValueError:
            return value.as_posix()
    return value.as_posix().removeprefix("./")


def evaluate(report: dict[str, Any], policy: dict[str, Any], root: Path = ROOT) -> dict[str, Any]:
    files: dict[str, Any] = {
        _relative(name, root): data for name, data in report.get("files", {}).items()
    }
    results: dict[str, dict[str, Any]] = {}
    for domain, settings in policy["domains"].items():
        expected = sorted({
            path.relative_to(root).as_posix()
            for pattern in settings["patterns"]
            for path in root.glob(pattern)
            if path.is_file() and path.suffix == ".py"
        })
        missing = [path for path in expected if path not in files]
        statements = covered = 0
        for path in expected:
            summary = files.get(path, {}).get("summary", {})
            count = summary.get("num_statements", 0)
            hit = summary.get("covered_lines", 0)
            if type(count) is not int or type(hit) is not int or count < 0 or hit < 0 or hit > count:
                raise ValueError(f"invalid coverage summary for {path}")
            statements += count
            covered += hit
        percent = round(100.0 * covered / statements, 2) if statements else 0.0
        floor = float(settings["minimum_percent"])
        passed = bool(expected) and not missing and statements > 0 and percent >= floor
        results[domain] = {
            "status": "PASS" if passed else "FAIL",
            "covered_statements": covered,
            "total_statements": statements,
            "coverage_percent": percent,
            "minimum_percent": floor,
            "files": len(expected),
            "missing_coverage_records": missing,
        }
    return {"status": "PASS" if all(x["status"] == "PASS" for x in results.values()) else "FAIL",
            "domains": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage", type=Path, default=ROOT / "coverage.json")
    parser.add_argument("--policy", type=Path, default=POLICY)
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root used to resolve policy patterns")
    parser.add_argument("--output", type=Path, help="write the deterministic domain report as JSON")
    args = parser.parse_args(argv)
    report = json.loads(args.coverage.read_text(encoding="utf-8"))
    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    result = evaluate(report, policy, args.root)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8", newline="\n")
    print(rendered, end="")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
