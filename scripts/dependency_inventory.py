"""Compare the pinned dependency set with installed package and license metadata."""
from __future__ import annotations

import argparse
import json
import re
from importlib import metadata
from pathlib import Path
from typing import Any


def parse_lock(path: Path) -> list[tuple[str, str]]:
    packages = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"\s*([A-Za-z0-9_.-]+)==([^;\s]+)\s*(?:#.*)?", line)
        if match:
            packages.append((match.group(1), match.group(2)))
    names = [name.lower().replace("_", "-").replace(".", "-") for name, _ in packages]
    if not packages or len(names) != len(set(names)):
        raise ValueError("lock file must contain unique pinned package versions")
    return packages


def build_report(lock_path: Path, distributions: Any = metadata.distributions) -> dict[str, Any]:
    installed = {}
    for dist in distributions():
        name = dist.metadata.get("Name")
        if not name:
            continue
        normalized = name.lower().replace("_", "-").replace(".", "-")
        installed[normalized] = dist
    rows = []
    for name, pinned_version in parse_lock(lock_path):
        dist = installed.get(name.lower().replace("_", "-").replace(".", "-"))
        if dist is None:
            rows.append({"name": name, "pinned_version": pinned_version, "installed_version": None,
                         "license": None, "status": "MISSING"})
            continue
        license_name = dist.metadata.get("License-Expression") or dist.metadata.get("License")
        if not license_name:
            classifiers = dist.metadata.get_all("Classifier") or []
            license_name = next((item.split("::", 1)[1].strip() for item in classifiers
                                 if item.startswith("License ::") and " :: " in item), "UNKNOWN")
        license_name = " ".join(str(license_name).split())[:200]
        version = dist.version
        rows.append({"name": name, "pinned_version": pinned_version, "installed_version": version,
                     "license": license_name, "status": "MATCH" if version == pinned_version else "VERSION_MISMATCH"})
    return {"schema_version": "fb.dependency-inventory.v1", "lock_file": lock_path.name,
            "locked_count": len(rows), "missing_count": sum(row["status"] == "MISSING" for row in rows),
            "version_mismatch_count": sum(row["status"] == "VERSION_MISMATCH" for row in rows),
            "unknown_license_count": sum(row["license"] in (None, "UNKNOWN") for row in rows),
            "packages": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=Path("requirements-lock.txt"))
    parser.add_argument("--output", type=Path, help="write the inventory as JSON")
    parser.add_argument("--check-versions", action="store_true", help="fail on missing or mismatched locked packages")
    parser.add_argument("--summary", action="store_true", help="print counts instead of the full JSON report")
    args = parser.parse_args()
    report = build_report(args.lock)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    if args.summary:
        print(json.dumps({key: report[key] for key in
                          ("locked_count", "missing_count", "version_mismatch_count", "unknown_license_count")},
                         sort_keys=True))
    else:
        print(encoded, end="")
    return int(bool(args.check_versions and (report["missing_count"] or report["version_mismatch_count"])))


if __name__ == "__main__":
    raise SystemExit(main())
