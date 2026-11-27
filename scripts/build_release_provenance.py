"""Build/check a content-addressed release provenance manifest (never implies release approval)."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/edge-program/release-provenance.current.json"
CHANGELOG = ROOT / "docs/edge-program/V3.0_PROGRESS.md"
RIGHTS = ROOT / "docs/edge-program/market-data-sources.v1.json"
RIGHTS_REVIEW = ROOT / "docs/edge-program/market-data-rights-review.v1.md"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> str:
    result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def source_dirty(generated_paths: set[str]) -> bool:
    """Ignore only declared test/report outputs; tracked and untracked source edits remain dirty."""
    entries = git("status", "--porcelain", "-z").split("\0")
    for entry in entries:
        if not entry:
            continue
        path = entry[3:].replace("\\", "/")
        if path not in generated_paths:
            return True
    return False


def worktree_digest(generated_paths: set[str]) -> str:
    """Hash all tracked and non-ignored untracked bytes, except emitted test/report artifacts."""
    tracked = git("ls-files", "-z").split("\0")
    untracked = git("ls-files", "--others", "--exclude-standard", "-z").split("\0")
    digest = hashlib.sha256()
    for relative in sorted({item.replace("\\", "/") for item in tracked + untracked if item}
                           - generated_paths):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        path = ROOT / relative
        if not path.is_file():
            digest.update(b"<deleted>\0")
            continue
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def build(test_artifacts: list[Path]) -> dict[str, Any]:
    strategy_files = sorted((ROOT / "src/financial_brain/strategies").rglob("*.py"))
    rule_files = [*strategy_files,
                  ROOT / "docs/sources/algorithm-source-registry.json",
                  ROOT / "docs/strategies/four_node/source/xauusd_4node_reference.pine",
                  ROOT / "docs/strategies/four_node/source/original_rules_2025-08-22.txt"]
    rules = [{"path": path.relative_to(ROOT).as_posix(), "sha256": sha256(path)} for path in rule_files]
    artifacts = []
    for path in test_artifacts:
        resolved = path if path.is_absolute() else ROOT / path
        if not resolved.is_file():
            raise ValueError(f"test artifact does not exist: {path}")
        artifacts.append({"path": resolved.relative_to(ROOT).as_posix(), "sha256": sha256(resolved)})
    rights = json.loads(RIGHTS.read_text(encoding="utf-8"))
    rights_counts: dict[str, int] = {}
    for source in rights.get("sources", []):
        status = str(source.get("status", "UNKNOWN"))
        rights_counts[status] = rights_counts.get(status, 0) + 1
    ignored_outputs = {OUTPUT.relative_to(ROOT).as_posix(),
                       "docs/edge-program/v3.0-status.json",
                       "docs/edge-program/v3.0-audit.jsonl",
                       "docs/edge-program/profit-readiness.json",
                       *((path if path.is_absolute() else ROOT / path).relative_to(ROOT).as_posix()
                         for path in test_artifacts)}
    dirty = source_dirty(ignored_outputs)
    manifest: dict[str, Any] = {
        "schema": "fb.release_provenance.v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "release_status": "NOT_RELEASED",
        "source": {"commit_sha": git("rev-parse", "HEAD"), "branch": git("branch", "--show-current"),
                   "worktree": "DIRTY" if dirty else "CLEAN",
                   "generated_outputs_excluded": sorted(ignored_outputs),
                   "worktree_content_sha256": worktree_digest(ignored_outputs)},
        "test_artifacts": artifacts,
        "changelog": {"path": CHANGELOG.relative_to(ROOT).as_posix(), "sha256": sha256(CHANGELOG),
                      "kind": "v3 checklist progress/change record"},
        "strategy_rule_hashes": rules,
        "data_rights_posture": {
            "registry_path": RIGHTS.relative_to(ROOT).as_posix(), "registry_sha256": sha256(RIGHTS),
            "review_path": RIGHTS_REVIEW.relative_to(ROOT).as_posix(), "review_sha256": sha256(RIGHTS_REVIEW),
            "source_status_counts": rights_counts,
            "clearance": "UNVERIFIED_OR_BLOCKED_UNTIL_SOURCE_SPECIFIC_RIGHTS_ARE_CONFIRMED",
            "credential_values_recorded": False,
        },
        "release_gates": {"clean_worktree": not dirty, "hosted_release_ci_attested": False,
                          "data_rights_cleared": False, "physical_orders_enabled": False},
        "manifest_sha256": None,
    }
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    manifest["manifest_sha256"] = hashlib.sha256(payload).hexdigest()
    return manifest


def verify(manifest: dict[str, Any]) -> None:
    claimed = manifest.get("manifest_sha256")
    unsigned = dict(manifest)
    unsigned["manifest_sha256"] = None
    digest = hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode("utf-8")).hexdigest()
    if claimed != digest:
        raise ValueError("release manifest hash mismatch")
    source = manifest["source"]
    if source["commit_sha"] != git("rev-parse", "HEAD") or source["branch"] != git("branch", "--show-current"):
        raise ValueError("release source commit or branch has changed")
    current_dirty = source_dirty(set(source.get("generated_outputs_excluded", [])))
    if source["worktree"] != ("DIRTY" if current_dirty else "CLEAN"):
        raise ValueError("release worktree state has changed")
    if source["worktree_content_sha256"] != worktree_digest(set(source["generated_outputs_excluded"])):
        raise ValueError("release source worktree content hash is stale")
    row = manifest["changelog"]
    if sha256(ROOT / row["path"]) != row["sha256"]:
        raise ValueError("changelog hash is stale")
    rights = manifest["data_rights_posture"]
    if sha256(ROOT / rights["registry_path"]) != rights["registry_sha256"]:
        raise ValueError("data-rights registry hash is stale")
    if sha256(ROOT / rights["review_path"]) != rights["review_sha256"]:
        raise ValueError("data-rights review hash is stale")
    for row in manifest["strategy_rule_hashes"]:
        if sha256(ROOT / row["path"]) != row["sha256"]:
            raise ValueError(f"strategy-rule hash is stale: {row['path']}")
    for row in manifest["test_artifacts"]:
        if sha256(ROOT / row["path"]) != row["sha256"]:
            raise ValueError(f"test-artifact hash is stale: {row['path']}")
    if manifest.get("release_status") != "NOT_RELEASED":
        raise ValueError("local provenance builder cannot approve a release")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-artifact", type=Path, action="append", default=[])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        manifest = json.loads(OUTPUT.read_text(encoding="utf-8"))
        verify(manifest)
        print(f"release provenance verified (not released): {manifest['manifest_sha256']}")
    else:
        manifest = build(args.test_artifact)
        OUTPUT.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote non-release provenance manifest with {len(manifest['test_artifacts'])} test artifact(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
