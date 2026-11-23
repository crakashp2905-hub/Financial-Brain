"""Build/check capability and source-commit provenance for the historical PR stack."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GRAPH_PATH = Path("docs/edge-program/v3.0-pr-graph-2026-11-23-r3.json")
OUTPUT = Path("docs/edge-program/capability-provenance.v1.json")
CAPABILITY_PATH = "docs/edge-program/capability-ownership.v1.json"
MAIN_SHA = "3c99b5dddc775db888822b4cec1849e5ebca56c8"
API_STATE = {
    "34": {"state": "open", "merged": False, "head_sha": "88153cd1cbae31b6070b3ab47bb80709226c23de", "base": "main", "base_sha": MAIN_SHA, "merge_commit_sha": "5ecee8d1556e11efd46cf180bd615b822ea215a2"},
    "36": {"state": "open", "merged": False, "head_sha": "3b858d1ce573e659d89f903d0bc8b648d5e1a4e3", "base": "feature/five-desk-d1-instruments", "base_sha": "8b0b12cef3e3cfa8afbd886d8d6892b942ebabf8", "merge_commit_sha": "1a5dda6bda83bfb066d4d65bcec80c11d7160d9c"},
    "37": {"state": "open", "merged": False, "head_sha": "8b0b12cef3e3cfa8afbd886d8d6892b942ebabf8", "base": "main", "base_sha": "5c7ae82a314b1e547d345e7b988e90f77d57224b", "merge_commit_sha": "c837d1bc4869d88448c43aa5a03c58c01e8fcb61"},
    "38": {"state": "open", "merged": False, "head_sha": "bdfb292ae2f45fc83e698084b2ac4fb1d6966842", "base": "feature/five-desk-d2-data-parity", "base_sha": "3b858d1ce573e659d89f903d0bc8b648d5e1a4e3", "merge_commit_sha": "ab6eb9027f351d71fe171dc3719354d5b66059b7"},
    "39": {"state": "open", "merged": False, "head_sha": "a1cdb2ce7b7b29facbf3da1630dd17e73076ba50", "base": "main", "base_sha": "5c7ae82a314b1e547d345e7b988e90f77d57224b", "merge_commit_sha": "5274b551350e5676e68adcc17434e01661af63da"},
    "40": {"state": "closed", "merged": True, "head_sha": "39817e98dea70640c26d7530dcf51a23d0cce2bb", "base": "main", "base_sha": "5c7ae82a314b1e547d345e7b988e90f77d57224b", "merge_commit_sha": "50248f1474f8f3476d723584da410a6fac7e4219"},
}


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _show(commit: str, path: str) -> bytes | None:
    result = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=ROOT, capture_output=True)
    return result.stdout if result.returncode == 0 else None


def _capability_snapshot(commit: str) -> dict[str, Any]:
    raw = _show(commit, CAPABILITY_PATH)
    if raw is None:
        return {"present": False, "sha256": None, "capability_ids": []}
    body = json.loads(raw)
    return {
        "present": True,
        "sha256": hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest(),
        "capability_ids": sorted(item["id"] for item in body.get("capabilities", [])),
        "module_count": len(body.get("module_inventory", [])),
    }


def build_manifest() -> dict[str, Any]:
    graph = json.loads((ROOT / GRAPH_PATH).read_text(encoding="utf-8"))
    sources: list[dict[str, Any]] = [{
        "source_id": "main",
        "kind": "baseline",
        "commit_sha": MAIN_SHA,
        "tree_sha": _git("rev-parse", f"{MAIN_SHA}^{{tree}}"),
        "capability_registry": _capability_snapshot(MAIN_SHA),
        "depends_on": [],
    }]
    for pr in ("34", "36", "37", "38", "39", "40"):
        node = graph["nodes"][pr]
        state = API_STATE[pr]
        head = state["head_sha"]
        base = state["base_sha"]
        if head != node["head_sha"] or base != node["base_sha"]:
            raise ValueError(f"PR #{pr} API snapshot and source graph disagree on exact refs")
        changed = _git("diff", "--name-only", f"{base}...{head}").splitlines()
        if set(changed) != set(node["changed_files"]):
            raise ValueError(f"PR #{pr} changed-file list differs from the captured graph")
        head_registry = _capability_snapshot(head)
        base_registry = _capability_snapshot(base)
        delta: dict[str, list[str]] | None = None
        map_commit = head if head_registry["present"] else MAIN_SHA
        if head_registry["present"] and base_registry["present"]:
            old_body = json.loads(_show(base, CAPABILITY_PATH) or b"{}")
            new_body = json.loads(_show(head, CAPABILITY_PATH) or b"{}")
            old = {item["id"]: item for item in old_body.get("capabilities", [])}
            new = {item["id"]: item for item in new_body.get("capabilities", [])}
            delta = {
                "added": sorted(set(new) - set(old)),
                "removed": sorted(set(old) - set(new)),
                "modified": sorted(key for key in set(old) & set(new) if old[key] != new[key]),
            }
            old_modules = {item["path"]: item for item in old_body.get("module_inventory", [])}
            new_modules = {item["path"]: item for item in new_body.get("module_inventory", [])}
            module_delta: dict[str, list[str]] | None = {
                "added": sorted(set(new_modules) - set(old_modules)),
                "removed": sorted(set(old_modules) - set(new_modules)),
                "modified": sorted(
                    key for key in set(old_modules) & set(new_modules)
                    if old_modules[key] != new_modules[key]
                ),
            }
        else:
            module_delta = None
        mapped: dict[str, list[str]] = {}
        # Resolve path changes against the registry that existed on the PR head; if the
        # old branch predates that registry, use main's map and label that mapping retrospective.
        map_registry = json.loads(_show(map_commit, CAPABILITY_PATH) or b"{}")
        source_path_to_caps: dict[str, set[str]] = {}
        for capability in map_registry.get("capabilities", []):
            for path in [capability.get("owner"), *capability.get("implementations", [])]:
                if path:
                    source_path_to_caps.setdefault(path, set()).add(capability["id"])
        module_map: dict[str, dict[str, str]] = {
            item["path"]: {
                "module_role": item.get("module_role", "unknown"),
                "module_owner": item.get("module_owner", "unassigned"),
            }
            for item in map_registry.get("module_inventory", [])
        }
        mapped_modules: dict[str, list[dict[str, str]]] = {}
        for path in changed:
            for capability_id in source_path_to_caps.get(path, set()):
                mapped.setdefault(capability_id, []).append(path)
            if path in module_map:
                mapped_modules.setdefault(module_map[path]["module_role"], []).append({
                    "path": path,
                    "module_owner": module_map[path]["module_owner"],
                })
        sources.append({
            "source_id": f"PR-{pr}",
            "kind": "pull_request",
            "pr_number": int(pr),
            "state": state["state"],
            "merged": state["merged"],
            "head_ref": node["head"],
            "head_sha": head,
            "base_ref": state["base"],
            "base_sha": base,
            "merge_commit_sha": state["merge_commit_sha"],
            "depends_on": [f"PR-{dep}" for dep in node.get("depends_on", [])],
            "changed_paths": changed,
            "capability_registry_at_base": base_registry,
            "capability_registry_at_head": head_registry,
            "capability_registry_delta": delta,
            "module_inventory_delta": module_delta,
            "capability_path_mapping": {
                "basis_commit": map_commit,
                "retrospective_mapping": not head_registry["present"],
                "capabilities_to_changed_paths": {key: sorted(value) for key, value in sorted(mapped.items())},
                "module_roles_to_changed_paths": {
                    key: sorted(value, key=lambda item: item["path"])
                    for key, value in sorted(mapped_modules.items())
                },
                "changed_paths_without_owner_or_module_mapping": sorted(
                    set(changed)
                    - {p for values in mapped.values() for p in values}
                    - {item["path"] for values in mapped_modules.values() for item in values}
                ),
            },
        })
    by_id = {item["source_id"]: item for item in sources}
    for dependent, prerequisite in (("PR-36", "PR-37"), ("PR-38", "PR-36")):
        if by_id[dependent]["base_sha"] != by_id[prerequisite]["head_sha"]:
            raise ValueError(f"{dependent} base does not equal {prerequisite} head")
    main_ancestor_check = subprocess.run(
        ["git", "merge-base", "--is-ancestor", API_STATE["40"]["merge_commit_sha"], MAIN_SHA],
        cwd=ROOT,
        capture_output=True,
    )
    if main_ancestor_check.returncode != 0:
        raise ValueError("PR #40 merge commit is not an ancestor of the captured current main")
    return {
        "schema": "fb.capability_source_provenance.v1",
        "captured_at_utc": "2026-11-23T16:24:48Z",
        "repository": "crakashp2905-hub/Financial-Brain",
        "main_sha_verified_from_github_api": MAIN_SHA,
        "source_graph": GRAPH_PATH.as_posix(),
        "capability_registry_path": CAPABILITY_PATH,
        "sources": sources,
        "dependency_edges": [
            {"from": "PR-37", "to": "PR-36", "basis": "PR #36 base SHA equals PR #37 head SHA"},
            {"from": "PR-36", "to": "PR-38", "basis": "PR #38 base SHA equals PR #36 head SHA"},
            {"from": "PR-40", "to": "main", "basis": "PR #40 merge commit is an ancestor of current main"},
        ],
        "limitations": [
            "Capability contract mappings on PR heads without their own capability registry are retrospective exact-path matches against the current main registry; they are not historical declarations from those PR authors.",
            "Changed path membership and ancestry do not transfer CI, review, or behavioral validation between commits.",
            "PR #39 path overlap and PR #40's description do not establish a commit dependency; the graph records no ancestry edge between them.",
        ],
    }


def validate() -> list[str]:
    try:
        actual = json.loads((ROOT / OUTPUT).read_text(encoding="utf-8"))
        expected = build_manifest()
    except (OSError, json.JSONDecodeError, subprocess.CalledProcessError, ValueError) as exc:
        return [f"cannot reproduce capability provenance: {exc}"]
    return [] if actual == expected else ["capability provenance manifest is stale; regenerate it with scripts/capability_provenance.py"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate the checked-in manifest")
    args = parser.parse_args()
    output = ROOT / OUTPUT
    if args.check:
        errors = validate()
        if errors:
            for error in errors:
                print(f"FAIL {error}")
            return 1
        print("capability provenance valid for main and PR #34, #36, #37, #38, #39, and #40")
        return 0
    output.write_text(json.dumps(build_manifest(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
