"""Bind curated capability maturity to the exact source and executable test files."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs/audits/capability-matrix.v1.json"
DEFAULT_OUT = ROOT / "docs/audits/capability-evidence.v1.json"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(matrix: dict[str, Any], root: Path = ROOT) -> dict[str, Any]:
    capabilities = []
    for item in sorted(matrix["capabilities"], key=lambda row: row["id"]):
        sources = {name: _sha(root / name) for name in sorted(item["source"])}
        tests = {name: _sha(root / name) for name in sorted(item["tests"])}
        evidence = {"id": item["id"], "maturity": item["maturity"], "sources": sources,
                    "tests": tests, "test_scope": "full-pytest-suite-required-by-ci"}
        evidence["artifact_sha256"] = hashlib.sha256(_canonical(evidence)).hexdigest()
        capabilities.append(evidence)
    report: dict[str, Any] = {"schema_version": "fb.capability-evidence.v1",
                              "matrix_sha256": hashlib.sha256(_canonical(matrix)).hexdigest(),
                              "capabilities": capabilities}
    report["manifest_sha256"] = hashlib.sha256(_canonical(report)).hexdigest()
    return report


def render(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="refresh the committed evidence manifest")
    group.add_argument("--check", action="store_true", help="fail if the committed manifest is stale")
    parser.add_argument("--matrix", type=Path, default=MATRIX)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
    report = build(matrix, ROOT)
    expected = render(report)
    if args.write:
        args.output.write_text(expected, encoding="utf-8")
        print(f"Wrote {len(report['capabilities'])} capability evidence records; manifest_sha256={report['manifest_sha256']}")
        return 0
    if not args.output.is_file() or args.output.read_text(encoding="utf-8") != expected:
        print("capability evidence manifest is missing or stale; run scripts/audit_capability_evidence.py --write")
        return 1
    print(f"Verified {len(report['capabilities'])} capability evidence records; manifest_sha256={report['manifest_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
