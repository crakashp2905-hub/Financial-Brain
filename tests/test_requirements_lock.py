from __future__ import annotations

from pathlib import Path

import pytest

from scripts.validate_requirements_lock import ROOT, validate


def test_lock_exactly_pins_all_runtime_and_dev_dependencies() -> None:
    validate()


def test_lock_fails_when_a_dev_dependency_is_missing(tmp_path: Path) -> None:
    lock = tmp_path / "requirements-lock.txt"
    source = (ROOT / "requirements-lock.txt").read_text(encoding="utf-8")
    lock.write_text("\n".join(line for line in source.splitlines()
                                if not line.startswith("coverage==")) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="coverage"):
        validate(ROOT / "pyproject.toml", lock)


def test_lock_rejects_unpinned_entries(tmp_path: Path) -> None:
    lock = tmp_path / "requirements-lock.txt"
    source = (ROOT / "requirements-lock.txt").read_text(encoding="utf-8")
    lock.write_text(source.replace("coverage==7.17.4", "coverage>=7.0"), encoding="utf-8")
    with pytest.raises(ValueError, match="exact == pin"):
        validate(ROOT / "pyproject.toml", lock)
