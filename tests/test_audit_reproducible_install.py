from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.audit_reproducible_install import audit


def _lock_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _repo(root: Path, *, source_text: str = "") -> None:
    (root / "docs/edge-program").mkdir(parents=True)
    (root / ".github/workflows").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "scripts").mkdir()
    (root / "requirements-lock.txt").write_text("package==1.0\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (root / "src/module.py").write_text(source_text, encoding="utf-8")
    lock_hash = _lock_hash(root / "requirements-lock.txt")
    (root / "docs/edge-program/reproducibility-contract.v1.json").write_text(
        json.dumps({"python_version": "3.13.5", "dependency_lock": {"path": "requirements-lock.txt", "sha256": lock_hash}}),
        encoding="utf-8",
    )
    (root / ".github/workflows/ci.yml").write_text(
        "\n".join(
            [
                "reproducibility:",
                "os: [ubuntu-24.04, windows-2022]",
                "cache-dependency-path: requirements-lock.txt",
                "python -m pip install -r requirements-lock.txt",
                "python scripts/verify_reproducibility.py",
                'python-version: "3.13.5"',
            ]
        ),
        encoding="utf-8",
    )


def test_valid_lock_platform_matrix_and_source_tree_pass(tmp_path: Path) -> None:
    _repo(tmp_path)

    assert audit(tmp_path) == []


def test_lock_hash_drift_and_hard_coded_user_path_fail(tmp_path: Path) -> None:
    _repo(tmp_path, source_text=r'CACHE = "C:\Users\operator\cache"' + "\n")
    (tmp_path / "requirements-lock.txt").write_text("package==2.0\n", encoding="utf-8")

    errors = audit(tmp_path)

    assert any("dependency lock hash" in error for error in errors)
    assert any("user-specific absolute path" in error for error in errors)


def test_missing_windows_locked_install_is_rejected(tmp_path: Path) -> None:
    _repo(tmp_path)
    workflow = tmp_path / ".github/workflows/ci.yml"
    workflow.write_text(workflow.read_text(encoding="utf-8").replace("windows-2022", "ubuntu-22.04"), encoding="utf-8")

    errors = audit(tmp_path)

    assert any("ubuntu-24.04, windows-2022" in error for error in errors)


def test_unpinned_lock_entry_is_rejected(tmp_path: Path) -> None:
    _repo(tmp_path)
    lock = tmp_path / "requirements-lock.txt"
    lock.write_text("package>=1.0\n", encoding="utf-8")
    contract = tmp_path / "docs/edge-program/reproducibility-contract.v1.json"
    data = json.loads(contract.read_text(encoding="utf-8"))
    data["dependency_lock"]["sha256"] = _lock_hash(lock)
    contract.write_text(json.dumps(data), encoding="utf-8")

    errors = audit(tmp_path)

    assert any("not an exact version pin" in error for error in errors)


def test_repository_contract_and_paths_are_current() -> None:
    assert audit() == []
