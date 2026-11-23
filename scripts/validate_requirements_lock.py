"""Ensure the full-test lock pins every project and development requirement."""
from __future__ import annotations

import argparse
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([^\s;]+)\s*(?:;.*)?$")
REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def validate(pyproject_path: Path = ROOT / "pyproject.toml",
             lock_path: Path = ROOT / "requirements-lock.txt") -> None:
    project = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    direct = [*project["project"]["dependencies"],
              *project["project"]["optional-dependencies"]["dev"]]
    locked: dict[str, str] = {}
    for line_number, line in enumerate(lock_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = PACKAGE_LINE.fullmatch(line)
        if match is None:
            raise ValueError(f"requirements lock line {line_number} is not an exact == pin")
        name, version = match.groups()
        key = _canonical(name)
        if key in locked:
            raise ValueError(f"duplicate requirements lock entry for {name}")
        locked[key] = version

    missing = sorted({
        _canonical(match.group(1))
        for raw in direct
        if (match := REQUIREMENT_NAME.match(raw)) and _canonical(match.group(1)) not in locked
    })
    if missing:
        raise ValueError("project runtime/dev requirements missing from exact lock: " + ", ".join(missing))


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    validate()
    print("requirements-lock.txt exactly pins every runtime and dev dependency")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
