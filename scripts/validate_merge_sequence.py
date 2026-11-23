"""Validate the exact-base dependency and stated merge gates for PRs 37, 36 and 38."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "docs/edge-program/canonical-merge-sequence.v1.json"


def validate(snapshot: dict) -> None:
    if snapshot.get("schema") != "fb.canonical_merge_sequence.v1":
        raise ValueError("unsupported merge-sequence schema")
    if snapshot.get("sequence") != [37, 36, 38]:
        raise ValueError("canonical sequence must be PR 37, then PR 36, then PR 38")
    nodes = snapshot.get("nodes")
    if not isinstance(nodes, dict) or set(nodes) != {"36", "37", "38"}:
        raise ValueError("snapshot must include exactly PRs 36, 37 and 38")
    for key, node in nodes.items():
        if node.get("state") != "open" or node.get("merged") is not False:
            raise ValueError(f"PR {key} merge state is malformed")
        for digest in (node.get("base_sha"), node.get("head_sha")):
            if not isinstance(digest, str) or len(digest) != 40 or set(digest) - set("0123456789abcdef"):
                raise ValueError(f"PR {key} base/head SHA must be a full lowercase Git SHA")
    p37, p36, p38 = (nodes[str(number)] for number in (37, 36, 38))
    if p36["base"] != p37["head"] or p36["base_sha"] != p37["head_sha"]:
        raise ValueError("PR 36 does not exactly depend on PR 37")
    if p38["base"] != p36["head"] or p38["base_sha"] != p36["head_sha"]:
        raise ValueError("PR 38 does not exactly depend on PR 36")
    if p37["base_sha"] == snapshot.get("main_sha_at_capture"):
        raise ValueError("snapshot should identify current-main drift when PR 37 base is stale")
    gates = "\n".join(snapshot.get("merge_gates", []))
    required = ("Rebase PR 37", "green hosted checks", "test-backed change log", "Refresh PR metadata")
    if any(text not in gates for text in required):
        raise ValueError("merge gates omit required current-main, CI, rebase, or refresh controls")
    if not snapshot.get("sources") or not snapshot.get("limitations"):
        raise ValueError("source URLs and limitations are required")


def main() -> int:
    try:
        validate(json.loads(SNAPSHOT.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"FAIL canonical merge-sequence validation: {exc}")
        return 1
    print("canonical PR merge sequence is consistent: 37 -> 36 -> 38")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
