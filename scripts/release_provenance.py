"""Create a release evidence manifest bound to the exact CI checkout and artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = Path("docs/edge-program/CHANGELOG.md")
PERMISSIONS = Path("docs/edge-program/data-permissions.v1.json")
RIGHTS_REVIEW = Path("docs/edge-program/market-data-rights-review.v1.md")
TEST_ARTIFACTS = ("pytest-results.xml", "coverage.xml", "critical-coverage.json",
                  "release-candidate-audit.json")
STRATEGY_PATHS = (
    "src/financial_brain/strategies",
    "src/financial_brain/patterns",
    "src/financial_brain/evaluation/swing.py",
    "src/financial_brain/paper/intraday.py",
    "src/financial_brain/paper/intraday_runner.py",
    "src/financial_brain/paper/zoo_edges.py",
    "src/financial_brain/forward_edge/registry.py",
    "src/financial_brain/forward_edge/strategy_adapter.py",
    "src/financial_brain/forward_edge/strategy_pipeline.py",
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], check=True,
                            capture_output=True, text=True, timeout=10)
    return result.stdout.strip()


def _files_under(root: Path, rel: str) -> list[Path]:
    path = root / rel
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(p for p in path.rglob("*.py") if p.is_file() and not p.is_symlink())
    raise ValueError(f"required strategy-rule path is missing: {rel}")


def build_manifest(root: Path = ROOT, *, output: Path | None = None) -> dict[str, Any]:
    """Hash required release inputs; fail rather than publish a partial manifest."""
    root = root.resolve()
    commit = _git(root, "rev-parse", "HEAD")
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise ValueError("Git HEAD is not a full commit SHA")
    tracked_changes = _git(root, "diff", "--name-only", "HEAD", "--", ".")
    if tracked_changes:
        raise ValueError("tracked source changes exist outside the tested commit")

    tests: dict[str, str] = {}
    for rel in TEST_ARTIFACTS:
        path = root / rel
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"required test artifact is missing: {rel}")
        tests[rel] = _sha256(path.read_bytes())

    strategy_rules: dict[str, str] = {}
    for rel in STRATEGY_PATHS:
        for path in _files_under(root, rel):
            key = path.relative_to(root).as_posix()
            strategy_rules[key] = _sha256(path.read_bytes().replace(b"\r\n", b"\n"))

    permissions_path = root / PERMISSIONS
    rights_review_path = root / RIGHTS_REVIEW
    changelog_path = root / CHANGELOG
    for path in (permissions_path, rights_review_path, changelog_path):
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"required release document is missing: {path.relative_to(root)}")
    permissions = json.loads(permissions_path.read_text(encoding="utf-8"))
    statuses = Counter(str(row.get("status", "MISSING")) for row in permissions.get("permissions", []))
    if not statuses:
        raise ValueError("data-rights register contains no permission records")
    unknown_count = sum(count for status, count in statuses.items() if status != "USE_ALLOWED")
    posture = ("ALL_RECORDS_MARKED_USE_ALLOWED_NOT_LEGAL_VALIDATION" if unknown_count == 0
               else "RESTRICTED_UNRESOLVED_RIGHTS")

    manifest = {
        "schema": "fb.release_provenance.v1",
        "source": {
            "commit_sha": commit,
            "branch": _git(root, "branch", "--show-current") or None,
            "tracked_tree_clean": True,
        },
        "test_artifacts": tests,
        "changelog": {
            "path": CHANGELOG.as_posix(),
            "sha256": _sha256(changelog_path.read_bytes().replace(b"\r\n", b"\n")),
        },
        "strategy_rules": {
            "files": strategy_rules,
            "file_count": len(strategy_rules),
        },
        "data_rights": {
            "posture": posture,
            "status_counts": dict(sorted(statuses.items())),
            "permissions_path": PERMISSIONS.as_posix(),
            "permissions_sha256": _sha256(permissions_path.read_bytes().replace(b"\r\n", b"\n")),
            "rights_review_path": RIGHTS_REVIEW.as_posix(),
            "rights_review_sha256": _sha256(rights_review_path.read_bytes().replace(b"\r\n", b"\n")),
            "authorization_inferred": False,
        },
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8", newline="\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("release-provenance.json"))
    args = parser.parse_args()
    manifest = build_manifest(ROOT, output=ROOT / args.output)
    print(f"release provenance written for {manifest['source']['commit_sha']}; "
          f"data rights={manifest['data_rights']['posture']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
