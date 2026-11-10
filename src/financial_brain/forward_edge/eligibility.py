"""Frozen eligibility list (FE-0003): every known strategy gets one immutable status, a lineage and an exclusion reason.

Sources: the 13 recorded trials (all FAILED) and the strategy modules in ``src/financial_brain/strategies``. No module has a
preregistered forward trial, so none is prospectively eligible; they are descriptive SHADOW_ONLY. The list is committed
as ``docs/forward-edge/strategy-eligibility.json``; a new strategy module changes the generated output and fails the test
until it is given a status on purpose.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

LEDGER_REL = "docs/trials/ledger.jsonl"
OUT_REL = "docs/forward-edge/strategy-eligibility.json"


def build(repo_root: Path) -> dict:
    root = Path(repo_root)
    entries = []
    for i, line in enumerate(l for l in (root / LEDGER_REL).read_text(encoding="utf-8").splitlines() if l.strip()):
        row = json.loads(line)
        if row.get("kind") != "trial":
            continue
        lineage = hashlib.sha256(json.dumps(row, sort_keys=True).encode("utf-8")).hexdigest()
        entries.append({"id": "TRIAL:" + row["name"], "status": "FAILED", "lineage": f"{LEDGER_REL}#sha256:{lineage}",
                        "exclusion_reason": f"recorded verdict {row['verdict']} failing {','.join(row['failed'])}",
                        "prospective_observation": False})
    for path in sorted((root / "src/financial_brain/strategies").glob("*.py")):
        if path.name == "__init__.py":
            continue
        entries.append({"id": "MODULE:" + path.stem, "status": "SHADOW_ONLY",
                        "lineage": path.relative_to(root).as_posix(),
                        "exclusion_reason": "no preregistered forward trial; 2026Q4 protected budget 13/13 spent",
                        "prospective_observation": False})
    return {"schema": "fb.forward_edge.eligibility.v1", "entries": entries}


def render(repo_root: Path) -> str:
    return json.dumps(build(repo_root), indent=1, sort_keys=True) + "\n"


def write(repo_root: Path) -> None:
    (Path(repo_root) / OUT_REL).write_text(render(repo_root), encoding="utf-8", newline="\n")
