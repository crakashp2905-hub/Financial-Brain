from __future__ import annotations

from email.message import Message
from pathlib import Path

import pytest

from scripts.dependency_inventory import build_report, parse_lock


class Distribution:
    def __init__(self, name: str, version: str, license_expression: str | None = None):
        self.metadata = Message()
        self.metadata["Name"] = name
        if license_expression:
            self.metadata["License-Expression"] = license_expression
        self.version = version


def test_lock_parser_requires_unique_exact_pins(tmp_path: Path) -> None:
    lock = tmp_path / "requirements-lock.txt"
    lock.write_text("alpha_pkg==1.2.3\nBeta==4.5 # pinned\n", encoding="utf-8")
    assert parse_lock(lock) == [("alpha_pkg", "1.2.3"), ("Beta", "4.5")]
    lock.write_text("Alpha_Pkg==1\nalpha-pkg==1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unique pinned"):
        parse_lock(lock)


def test_uv_export_parser_selects_only_current_environment_marked_pins(tmp_path: Path) -> None:
    lock = tmp_path / "requirements-lock.txt"
    continuation = chr(92)
    lock.write_text("\n".join([
        f"numpy==1.0 ; python_full_version < '3.12' {continuation}",
        f"    --hash=sha256:old {continuation}",
        f"numpy==2.0 ; python_full_version >= '3.12' {continuation}",
        "    --hash=sha256:new",
    ]) + "\n", encoding="utf-8")

    versions = {version for _, version in parse_lock(lock)}
    assert len(versions) == 1
    assert versions <= {"1.0", "2.0"}


def test_inventory_reports_version_and_license_gaps_without_hiding_them(tmp_path: Path) -> None:
    lock = tmp_path / "lock.txt"
    lock.write_text("alpha==1.0\nbeta==2.0\ngamma==3.0\n", encoding="utf-8")
    report = build_report(lock, lambda: [Distribution("alpha", "1.0", "MIT"),
                                         Distribution("beta", "2.1")])

    assert report["locked_count"] == 3
    assert report["missing_count"] == 1
    assert report["version_mismatch_count"] == 1
    assert report["unknown_license_count"] == 2  # one unknown installed license and one absent package
    assert [row["status"] for row in report["packages"]] == ["MATCH", "VERSION_MISMATCH", "MISSING"]
