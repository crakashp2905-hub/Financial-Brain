"""Build or verify the deterministic v3 checklist evidence snapshot bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from financial_brain.forward_edge.evidence_bundle import export_evidence_bundle

INPUTS = ROOT / "docs/edge-program/release-evidence-inputs.v1.json"
OUTPUT = ROOT / "docs/edge-program/releases/unified-checklist-v3.0-c0fbd419.zip"


def _git_snapshot(commit: str, rel: str) -> bytes:
    result = subprocess.run(
        ["git", "show", f"{commit}:{rel}"], cwd=ROOT, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return result.stdout.replace(b"\r\n", b"\n")


def build_bytes() -> bytes:
    manifest = json.loads(INPUTS.read_text(encoding="utf-8"))
    commit = manifest["snapshot_commit"]
    paths = manifest["snapshot_paths"]
    tools = manifest["local_tools_included"]
    if len(paths) != len(set(paths)) or len(tools) != len(set(tools)):
        raise ValueError("release input paths must be unique")
    # Keep the temporary archive on the same workspace volume: Windows sandbox temp
    # redirection may make os.replace across temp volumes fail.
    with tempfile.TemporaryDirectory(prefix=".fb-checklist-evidence-", dir=ROOT) as temp_name:
        temp = Path(temp_name)
        staged: dict[str, Path] = {}
        for rel in paths:
            canonical = _git_snapshot(commit, rel)
            archive_name = "github/" + rel[len(".github/"):] if rel.startswith(".github/") else rel
            staged[archive_name] = temp / archive_name
            staged[archive_name].parent.mkdir(parents=True, exist_ok=True)
            staged[archive_name].write_bytes(canonical)
        for rel in tools:
            source = ROOT / rel
            if not source.is_file() or source.is_symlink():
                raise ValueError(f"local release tool must be a regular file: {rel}")
            archive_name = "github/" + rel[len(".github/"):] if rel.startswith(".github/") else rel
            staged[archive_name] = temp / archive_name
            staged[archive_name].parent.mkdir(parents=True, exist_ok=True)
            staged[archive_name].write_bytes(source.read_bytes().replace(b"\r\n", b"\n"))
        staged[INPUTS.relative_to(ROOT).as_posix()] = temp / INPUTS.relative_to(ROOT)
        staged[INPUTS.relative_to(ROOT).as_posix()].parent.mkdir(parents=True, exist_ok=True)
        staged[INPUTS.relative_to(ROOT).as_posix()].write_bytes(INPUTS.read_bytes().replace(b"\r\n", b"\n"))
        output = temp / "bundle.zip"
        export_evidence_bundle(
            files=staged,
            output=output,
            reproduction_command=tuple(manifest["reproduction_command"]),
        )
        return output.read_bytes()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the committed archive and checksum")
    args = parser.parse_args(argv)
    expected = build_bytes()
    expected_hash = hashlib.sha256(expected).hexdigest()
    checksum_path = OUTPUT.with_suffix(OUTPUT.suffix + ".sha256")
    expected_checksum = f"{expected_hash}  {OUTPUT.name}\n"
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_bytes() != expected:
            raise SystemExit("checklist evidence bundle is missing or stale; rebuild it")
        if not checksum_path.is_file() or checksum_path.read_text(encoding="ascii") != expected_checksum:
            raise SystemExit("checklist evidence bundle checksum is missing or stale")
        print(f"Checklist evidence bundle reproduced: sha256={expected_hash}")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(expected)
    checksum_path.write_text(expected_checksum, encoding="ascii", newline="\n")
    print(f"Wrote {OUTPUT.relative_to(ROOT).as_posix()} sha256={expected_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
