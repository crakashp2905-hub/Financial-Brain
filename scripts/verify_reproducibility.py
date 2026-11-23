"""Verify the pinned Python, deterministic seed, timezone, lock, and fixture bytes."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from packaging.requirements import Requirement
try:
    from scripts.text_hash import sha256_lf_normalized
except ModuleNotFoundError:  # direct `python scripts/verify_reproducibility.py` invocation
    from text_hash import sha256_lf_normalized


CONTRACT = Path("docs/edge-program/reproducibility-contract.v1.json")


def sha256(path: Path) -> str:
    return sha256_lf_normalized(path.read_bytes())


def verify(*, integrity_only: bool = False) -> list[str]:
    errors: list[str] = []
    try:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"cannot read reproducibility contract: {exc}"]
    if contract.get("schema") != "fb.edge_program.reproducibility_contract.v1":
        return ["invalid reproducibility contract schema"]
    if contract.get("hash_newline_normalization") != "CRLF_TO_LF":
        errors.append("reproducibility contract must specify CRLF_TO_LF hash normalization")
    lock = contract["dependency_lock"]
    fixture = contract["deterministic_fixture"]
    for record in (lock, fixture):
        path = Path(record["path"])
        if not path.is_file():
            errors.append(f"missing reproducibility input: {path}")
        elif sha256(path) != record["sha256"]:
            errors.append(f"SHA-256 mismatch: {path}")
    if integrity_only:
        return errors

    expected_python = tuple(int(part) for part in contract["python_version"].split("."))
    if sys.version_info[:3] != expected_python:
        errors.append(f"Python is {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}; expected {contract['python_version']}")
    if os.environ.get("PYTHONHASHSEED") != contract["python_hash_seed"]:
        errors.append("PYTHONHASHSEED does not match the reproducibility contract")
    if os.environ.get("TZ") != contract["timezone_environment"]:
        errors.append("TZ does not match the reproducibility contract")
    if datetime.now().astimezone().utcoffset() != timedelta(0):
        errors.append("process local timezone is not UTC")

    for package, expected in contract["build_tools"].items():
        try:
            actual = version(package)
        except PackageNotFoundError:
            errors.append(f"build tool is not installed: {package}")
        else:
            if actual != expected:
                errors.append(f"{package} is {actual}; expected {expected}")

    lock_path = Path(lock["path"])
    for line in lock_path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        try:
            requirement = Requirement(value)
            actual = version(requirement.name)
        except (PackageNotFoundError, ValueError) as exc:
            errors.append(f"locked dependency unavailable ({value}): {exc}")
            continue
        if actual not in requirement.specifier:
            errors.append(f"{requirement.name} is {actual}; lock requires {requirement.specifier}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--integrity-only", action="store_true",
                        help="check contract and fixture hashes without asserting the active Python environment")
    args = parser.parse_args()
    errors = verify(integrity_only=args.integrity_only)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    suffix = " (hashes only)" if args.integrity_only else ""
    print(f"Reproducibility contract verified{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
