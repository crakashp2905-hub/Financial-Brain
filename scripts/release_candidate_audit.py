"""Audit checkout portability and report test/external-prerequisite evidence."""
from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = ("src", "scripts", ".github")
SCAN_FILES = ("pyproject.toml", "requirements-lock.txt")
DEVELOPER_PATH = re.compile(
    r"(?i)(?:\b[a-z]:[\\/](?:users|documents and settings)[\\/](?!me[\\/])[^\s\"']+"
    r"|\b[a-z]:[\\/]finance-brain(?:[\\/][^\s\"']*)?"
    r"|/(?:home|Users)/(?!runner(?:/|\b)|ubuntu(?:/|\b))[^\s\"']+)"
)


def _count(root: ET.Element, key: str) -> int:
    direct = root.get(key)
    if direct is not None:
        return int(direct)
    return sum(int(node.get(key, "0")) for node in root.findall(".//testsuite"))


def _source_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    for rel in SCAN_ROOTS:
        base = root / rel
        if base.exists():
            paths.extend(path for path in base.rglob("*") if path.is_file() and not path.is_symlink())
    paths.extend(root / rel for rel in SCAN_FILES if (root / rel).is_file())
    return sorted(set(paths))


def audit(root: Path, *, junit: Path, readiness: Path) -> dict[str, Any]:
    root = root.resolve()
    findings = []
    for path in _source_paths(root):
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeError, OSError):
            continue
        content = content.replace("\\\\", "\\")
        for number, line in enumerate(content.splitlines(), start=1):
            if DEVELOPER_PATH.search(line):
                findings.append({"path": path.relative_to(root).as_posix(), "line": number,
                                 "reason": "developer-specific absolute path"})

    junit_path = junit if junit.is_absolute() else root / junit
    test_results: dict[str, Any]
    if not junit_path.is_file():
        test_results = {"status": "MISSING", "tests": 0, "skipped": 0, "failures": None, "errors": None}
    else:
        try:
            result_root = ET.parse(junit_path).getroot()
            counts = {key: _count(result_root, key) for key in ("tests", "skipped", "failures", "errors")}
            okay = counts["tests"] > 0 and counts["failures"] == 0 and counts["errors"] == 0
            test_results = {"status": "PASS" if okay else "FAIL", **counts}
        except (ET.ParseError, OSError, ValueError) as exc:
            test_results = {"status": "INVALID", "tests": 0, "skipped": 0,
                            "failures": None, "errors": None, "detail": str(exc)}

    readiness_path = readiness if readiness.is_absolute() else root / readiness
    if not readiness_path.is_file():
        raise ValueError(f"profit-readiness report is missing: {readiness_path}")
    readiness_report = json.loads(readiness_path.read_text(encoding="utf-8"))
    gates = [{"id": row.get("id"), "status": row.get("status"), "summary": row.get("summary")}
             for row in readiness_report.get("gates", []) if row.get("status") != "GREEN"]
    permission_file = root / "docs/edge-program/data-permissions.v1.json"
    permissions = json.loads(permission_file.read_text(encoding="utf-8"))
    right_statuses = dict(sorted(Counter(str(row.get("status", "MISSING"))
                                         for row in permissions.get("permissions", [])).items()))
    unresolved_rights = sum(count for status, count in right_statuses.items() if status != "USE_ALLOWED")

    engineering_pass = not findings and test_results["status"] == "PASS"
    external_open = bool(gates or unresolved_rights)
    return {
        "schema": "fb.release_candidate_audit.v1",
        "engineering_candidate": "PASS" if engineering_pass else "FAIL",
        "developer_path_findings": findings,
        "test_coverage_evidence": {"source": junit_path.name, **test_results},
        "external_prerequisites": {
            "profit_readiness_gates_not_green": gates,
            "market_data_rights_status_counts": right_statuses,
            "unresolved_market_data_rights_count": unresolved_rights,
            "status": "OPEN" if external_open else "NO_RECORDED_BLOCKERS",
        },
        "release_candidate": "BLOCKED_EXTERNAL_OR_PERFORMANCE_PREREQUISITES" if engineering_pass and external_open
                             else "ENGINEERING_CHECKS_FAILED" if not engineering_pass else "READY_FOR_REVIEW",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", type=Path, default=Path("pytest-results.xml"))
    parser.add_argument("--readiness", type=Path, default=Path("docs/edge-program/profit_readiness.json"))
    parser.add_argument("--output", type=Path, default=Path("release-candidate-audit.json"))
    args = parser.parse_args()
    report = audit(ROOT, junit=args.junit, readiness=args.readiness)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8", newline="\n")
    print(f"engineering_candidate={report['engineering_candidate']} "
          f"release_candidate={report['release_candidate']} "
          f"external_prerequisites={report['external_prerequisites']['status']}")
    return 0 if report["engineering_candidate"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
