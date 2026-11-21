"""Summarize pytest's JUnit XML in GitHub Actions without hiding job failures."""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _integer(root: ET.Element, name: str) -> int:
    """Read a JUnit count from either the root suite or its child suites."""
    value = root.get(name)
    if value is not None:
        return int(value)
    return sum(int(suite.get(name, "0")) for suite in root.findall(".//testsuite"))


def main() -> int:
    report = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("pytest-results.xml")
    if not report.is_file():
        print(f"JUnit report is missing: {report}", file=sys.stderr)
        return 2

    try:
        root = ET.parse(report).getroot()
        counts = {key: _integer(root, key) for key in ("tests", "skipped", "failures", "errors")}
    except (ET.ParseError, OSError, ValueError) as exc:
        print(f"Could not read JUnit report {report}: {exc}", file=sys.stderr)
        return 2

    summary = (
        "## Pytest results\n\n"
        f"| Tests | Skipped | Failures | Errors |\n|---:|---:|---:|---:|\n"
        f"| {counts['tests']} | {counts['skipped']} | {counts['failures']} | {counts['errors']} |\n"
    )
    print(summary, end="")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8", newline="\n") as output:
            output.write(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
