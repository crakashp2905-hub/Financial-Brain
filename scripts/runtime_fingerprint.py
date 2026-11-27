"""Capture and verify repository/runtime provenance for a Financial Brain checkout.

The manifest intentionally records credential presence only, never credential values.
Unknown scheduler or entitlement state is represented as unknown and fails verification.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LOCKFILE = ROOT / "requirements-lock.txt"
OUTPUT = ROOT / "docs/edge-program/runtime-fingerprint-current.json"
SENSITIVE_ENV = ("KITE_API_KEY", "KITE_ACCESS_TOKEN", "FB_TIMESTAMP_PROVIDER", "FB_TRUST_ROOTS")


class FingerprintError(RuntimeError):
    """The checkout or its required runtime evidence could not be verified."""


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=False)


def _git(*args: str) -> str:
    result = _run("git", *args)
    if result.returncode:
        raise FingerprintError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _tree_digest(excluded: Path) -> str:
    """Hash bytes for tracked and non-ignored untracked files, excluding this output."""
    tracked = _git("ls-files", "-z").split("\0")
    untracked = _git("ls-files", "--others", "--exclude-standard", "-z").split("\0")
    paths = sorted({item for item in tracked + untracked if item})
    digest = hashlib.sha256()
    excluded_resolved = excluded.resolve()
    for relative in paths:
        path = ROOT / relative
        if path.resolve() == excluded_resolved:
            continue
        digest.update(relative.replace("\\", "/").encode("utf-8"))
        digest.update(b"\0")
        try:
            content_hash = hashlib.sha256(path.read_bytes()).digest()
        except FileNotFoundError:
            content_hash = b"MISSING"
        digest.update(content_hash)
        digest.update(b"\0")
    return digest.hexdigest()


def _scheduled_tasks() -> dict[str, Any]:
    """Read task names/actions and derive checkout paths; preserve discovery failures."""
    powershell = _run(
        "powershell", "-NoProfile", "-NonInteractive", "-Command",
        "$ErrorActionPreference='Stop'; Get-ScheduledTask | ForEach-Object { "
        "$t=$_; foreach($a in $t.Actions) { [pscustomobject]@{TaskPath=$t.TaskPath; "
        "TaskName=$t.TaskName; State=[string]$t.State; Execute=$a.Execute; "
        "Arguments=$a.Arguments; WorkingDirectory=$a.WorkingDirectory} } } | "
        "ConvertTo-Json -Compress -Depth 4",
    )
    if powershell.returncode == 0:
        try:
            parsed = json.loads(powershell.stdout or "[]")
            tasks = parsed if isinstance(parsed, list) else [parsed]
            if tasks:
                return {"status": "OBSERVED", "tasks": tasks, "error": None}
        except json.JSONDecodeError as exc:
            return {"status": "UNKNOWN", "tasks": [], "error": f"invalid task query JSON: {exc}"}

    schtasks = _run("schtasks.exe", "/Query", "/FO", "CSV", "/V")
    if schtasks.returncode == 0:
        try:
            rows = list(csv.DictReader(io.StringIO(schtasks.stdout)))
            return {"status": "OBSERVED", "tasks": rows, "error": None}
        except csv.Error as exc:
            return {"status": "UNKNOWN", "tasks": [], "error": f"invalid schtasks CSV: {exc}"}
    return {
        "status": "UNKNOWN", "tasks": [],
        "error": "PowerShell scheduled-task query failed: " + powershell.stderr.strip()
        + "; schtasks query failed: " + schtasks.stderr.strip(),
    }


def _entitlements() -> dict[str, Any]:
    matrix_path = ROOT / "docs/forward-edge/capability-matrix.json"
    matrix: dict[str, Any] = {}
    if matrix_path.exists():
        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    known_blockers = sorted({
        str(reason)
        for row in matrix.get("capabilities", [])
        if isinstance(row, dict)
        for reason in row.get("blockers", [])
        if "DATA" in str(reason).upper() or "ENTITLEMENT" in str(reason).upper()
    })
    return {
        "status": "UNKNOWN",  # repo documentation is not proof of the account's current license
        "credential_presence_only": {name: bool(os.environ.get(name)) for name in SENSITIVE_ENV},
        "documented_data_blockers": known_blockers,
        "source_policy_sha256": hashlib.sha256(matrix_path.read_bytes()).hexdigest()
        if matrix_path.exists() else None,
    }


def capture(output: Path) -> dict[str, Any]:
    output = output.resolve()
    head = _git("rev-parse", "HEAD")
    output_relative = output.relative_to(ROOT.resolve()).as_posix()
    raw_status = _git("status", "--porcelain=v1", "--untracked-files=all")
    status_lines = []
    for line in raw_status.splitlines():
        path_text = line[3:].split(" -> ")[-1].replace("\\", "/")
        if path_text != output_relative:
            status_lines.append(line)
    lock_hash = hashlib.sha256(LOCKFILE.read_bytes()).hexdigest() if LOCKFILE.exists() else None
    try:
        local_now = datetime.now().astimezone()
        current_date = local_now.date().isoformat()
    except OSError:
        current_date = None
    scheduler = _scheduled_tasks()
    entitlements = _entitlements()
    complete = scheduler["status"] == "OBSERVED" and entitlements["status"] == "CONFIRMED"
    return {
        "schema": "fb.runtime_fingerprint.v1",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repository_root": str(ROOT),
        "git": {
            "head": head, "branch": _git("branch", "--show-current"),
            "dirty": bool(status_lines), "status_porcelain_v1": status_lines,
            "working_tree_sha256": _tree_digest(output),
        },
        "runtime": {
            "python": sys.version.replace("\n", " "),
            "implementation": platform.python_implementation(),
            "os": platform.platform(), "machine": platform.machine(),
            "current_local_date": current_date,
        },
        "lockfile": {"path": str(LOCKFILE.relative_to(ROOT)), "sha256": lock_hash},
        "datasource_entitlements": entitlements,
        "scheduled_tasks": scheduler,
        "verification": {
            "status": "COMPLETE" if complete else "INCOMPLETE",
            "required_for_complete": ["scheduler inventory observed", "account/provider entitlements confirmed"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--verify", action="store_true", help="compare current state with the saved manifest")
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    observed = capture(output)
    if args.verify:
        if not output.exists():
            raise FingerprintError(f"fingerprint manifest not found: {output}")
        expected = json.loads(output.read_text(encoding="utf-8"))
        fields = ("git", "runtime", "lockfile", "datasource_entitlements", "scheduled_tasks")
        mismatches = [field for field in fields if observed[field] != expected.get(field)]
        if mismatches:
            raise FingerprintError("runtime fingerprint mismatch: " + ", ".join(mismatches))
        if observed["verification"]["status"] != "COMPLETE":
            raise FingerprintError("runtime evidence incomplete: scheduler or entitlement evidence is unknown")
        print(f"runtime fingerprint verified: {output}")
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(observed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"runtime fingerprint {observed['verification']['status']}: {output}")
    return 0 if observed["verification"]["status"] == "COMPLETE" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FingerprintError as exc:
        print(f"runtime fingerprint failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
