"""Check the pinned Linux/Windows install contract and reject user-path leaks."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
_USER_PATH = re.compile(
    r"(?i)(?:\b[A-Z]:\\[^\s\"']+|"
    r"/(?:home|Users)/[^/\s\"']+(?:/[^\s\"']*)?)"
)
_SOURCE_SUFFIXES = {".py", ".toml", ".txt", ".yaml", ".yml", ".json"}


def _normalized_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def audit(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    contract_path = root / "docs/edge-program/reproducibility-contract.v1.json"
    workflow_path = root / ".github/workflows/ci.yml"
    try:
        contract: dict[str, Any] = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"cannot load reproducibility contract: {exc}"]

    lock_record = contract.get("dependency_lock", {})
    lock_rel = lock_record.get("path")
    if not isinstance(lock_rel, str):
        errors.append("reproducibility contract has no dependency-lock path")
        lock_path = None
    else:
        lock_path = root / lock_rel
        if not lock_path.is_file():
            errors.append(f"dependency lock is missing: {lock_rel}")
        elif _normalized_sha256(lock_path) != lock_record.get("sha256"):
            errors.append(f"dependency lock hash does not match its audited contract: {lock_rel}")
        else:
            for line_number, line in enumerate(lock_path.read_text(encoding="utf-8").splitlines(), start=1):
                value = line.strip()
                if not value or value.startswith("#"):
                    continue
                if not re.fullmatch(r"[A-Za-z0-9_.-]+==[^;\s]+", value):
                    errors.append(f"dependency lock entry is not an exact version pin at {lock_rel}:{line_number}")

    version = contract.get("python_version")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        errors.append("reproducibility contract must pin an exact Python patch version")

    workflow = ""
    try:
        workflow = workflow_path.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"cannot load CI workflow: {exc}")
    required_fragments = (
        "reproducibility:",
        "os: [ubuntu-24.04, windows-2022]",
        "cache-dependency-path: requirements-lock.txt",
        "python -m pip install -r requirements-lock.txt",
        "python scripts/verify_reproducibility.py",
        f'python-version: "{version}"',
    )
    for fragment in required_fragments:
        if fragment not in workflow:
            errors.append(f"CI does not provide the locked Linux/Windows install step: {fragment}")

    scan_roots = (root / "src", root / "scripts", root / ".github")
    files = [root / "pyproject.toml", root / "requirements-lock.txt", contract_path]
    for scan_root in scan_roots:
        if scan_root.exists():
            files.extend(path for path in scan_root.rglob("*") if path.is_file() and path.suffix.lower() in _SOURCE_SUFFIXES)
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for match in _USER_PATH.finditer(text):
            rel = path.relative_to(root).as_posix()
            errors.append(f"user-specific absolute path in {rel}: {match.group(0)}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check the repository install contract")
    parser.parse_args()
    errors = audit()
    if errors:
        print("\n".join(errors))
        return 1
    print("audited lock, Linux/Windows reproducibility workflow, and source paths verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
