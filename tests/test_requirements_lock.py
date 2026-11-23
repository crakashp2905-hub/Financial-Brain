from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from scripts.validate_requirements_lock import ROOT, validate, validate_forecasting_lock


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


def test_optional_forecasting_test_dependency_is_pinned() -> None:
    validate_forecasting_lock()


def test_forecasting_lock_rejects_missing_or_floating_torch() -> None:
    with TemporaryDirectory(prefix="forecasting-lock-test-", dir=ROOT) as temp:
        lock = Path(temp) / "requirements-forecasting-lock.txt"
        lock.write_text("fsspec==2025.12.0\n", encoding="utf-8")
        with pytest.raises(ValueError, match="torch"):
            validate_forecasting_lock(ROOT / "pyproject.toml", lock)
        lock.write_text("torch>=2.2\n", encoding="utf-8")
        with pytest.raises(ValueError, match="exact == pin"):
            validate_forecasting_lock(ROOT / "pyproject.toml", lock)
