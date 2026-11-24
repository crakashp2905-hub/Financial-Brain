from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_ci_summary_reports_junit_counts_and_appends_github_summary(tmp_path: Path) -> None:
    report = tmp_path / "results.xml"
    report.write_text(
        '<testsuites tests="4" skipped="1" failures="1" errors="0">'
        '<testsuite tests="4" skipped="1" failures="1" errors="0" />'
        "</testsuites>",
        encoding="utf-8",
    )
    summary = tmp_path / "summary.md"
    env = os.environ.copy()
    env["GITHUB_STEP_SUMMARY"] = str(summary)

    result = subprocess.run(
        [sys.executable, str(Path(__file__).parents[1] / "scripts" / "ci_test_summary.py"), str(report)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    assert "| 4 | 1 | 1 | 0 |" in result.stdout
    assert "| 4 | 1 | 1 | 0 |" in summary.read_text(encoding="utf-8")


def test_ci_summary_fails_clearly_when_junit_report_is_missing(tmp_path: Path) -> None:
    missing = tmp_path / "missing.xml"
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parents[1] / "scripts" / "ci_test_summary.py"), str(missing)],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )

    assert result.returncode == 2
    assert "JUnit report is missing" in result.stderr
