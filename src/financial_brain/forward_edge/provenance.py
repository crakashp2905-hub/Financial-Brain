"""Best-effort repository provenance for auditable paper records.

This captures the local source state that produced a record. It is not an external
timestamp, signature, clean-tree guarantee, or proof that the code was deployed.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


def repository_provenance(root: Path | None = None, *, entry_point: str) -> dict[str, str | None]:
    """Capture HEAD, branch ancestry and a stable executable-entry-point identifier."""
    repo = root or Path(__file__).resolve().parents[3]

    def git(*args: str) -> str | None:
        try:
            value = subprocess.run(
                ["git", "-C", str(repo), *args], check=True, capture_output=True,
                text=True, timeout=2,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
        return value or None

    head = git("rev-parse", "HEAD")
    branch = git("branch", "--show-current")
    status = git("status", "--porcelain")
    parents = git("rev-list", "--parents", "-n", "1", "HEAD")
    ancestry = None
    if parents:
        fields = parents.split()
        ancestry = hashlib.sha256(json.dumps(fields, separators=(",", ":")).encode()).hexdigest()
    return {
        "source_commit": head,
        "source_branch": branch,
        "source_tree_state": "DIRTY" if status else ("CLEAN" if head else "UNKNOWN"),
        "branch_ancestry_sha256": ancestry,
        "executable_entry_point": entry_point,
    }
