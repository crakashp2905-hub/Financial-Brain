"""Build/check the release-blocking forward evidence gate report."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from financial_brain.forward_edge.forward_evidence_gate import evaluate_forward_evidence_gate


INPUT = ROOT / "docs/edge-program/forward-evidence-gate-input.v1.json"
OUTPUT = ROOT / "docs/edge-program/forward-evidence-gate.v1.json"


def build(input_path: Path = INPUT) -> dict[str, Any]:
    raw = input_path.read_bytes().replace(b"\r\n", b"\n")
    payload = json.loads(raw.decode("utf-8"))
    if payload.get("schema") != "fb.forward-evidence-gate-input.v1":
        raise ValueError("unsupported forward evidence gate input schema")
    expected = {"schema", "plan", "sessions", "attribution", "benchmark"}
    if set(payload) != expected:
        raise ValueError("forward evidence gate input fields differ from the versioned contract")
    report = evaluate_forward_evidence_gate(
        plan=payload["plan"], sessions=payload["sessions"],
        attribution=payload["attribution"], benchmark=payload["benchmark"])
    return report | {"input_sha256": hashlib.sha256(raw).hexdigest()}


def rendered(input_path: Path = INPUT) -> bytes:
    return (json.dumps(build(input_path), indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=INPUT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    input_path = args.input if args.input.is_absolute() else ROOT / args.input
    output_path = args.output if args.output.is_absolute() else ROOT / args.output
    expected = rendered(input_path)
    if args.check:
        if not output_path.is_file() or output_path.read_bytes().replace(b"\r\n", b"\n") != expected:
            raise ValueError(f"forward evidence gate report is stale: {output_path}")
        print(f"forward evidence gate verified: {json.loads(expected)['status']}")
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(expected)
        print(f"forward evidence gate written: {output_path}; {json.loads(expected)['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
