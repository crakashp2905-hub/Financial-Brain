"""Freeze tracked research-result notes and expose missing run provenance."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RESULTS = Path("brain/07-Research")
OUTPUT = Path("docs/edge-program/research-outcome-freeze.v1.json")
REQUIRED_METADATA = (
    "run_id",
    "cost_assumptions",
    "train_period",
    "validation_period",
    "test_period",
    "exposure",
)


def _front_matter(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    try:
        end = lines.index("---", 1)
    except ValueError:
        return ""
    return "\n".join(lines[1:end])


def _has_key(front_matter: str, key: str) -> bool:
    return re.search(rf"^\s*{re.escape(key)}\s*:", front_matter, re.MULTILINE) is not None


def build_inventory(root: Path = ROOT) -> dict[str, Any]:
    result_dir = root / RESULTS
    files = sorted(result_dir.glob("Result*.md"), key=lambda path: path.as_posix().casefold())
    records: list[dict[str, Any]] = []
    for path in files:
        raw = path.read_bytes()
        normalized = raw.replace(b"\r\n", b"\n")
        text = normalized.decode("utf-8")
        front = _front_matter(text)
        rel = path.relative_to(root).as_posix()
        git_blob = hashlib.sha1(b"blob " + str(len(normalized)).encode("ascii") + b"\0" + normalized).hexdigest()
        records.append({
            "path": rel,
            "git_blob_sha1": git_blob,
            "sha256_lf": hashlib.sha256(normalized).hexdigest(),
            "required_machine_metadata": {key: _has_key(front, key) for key in REQUIRED_METADATA},
        })

    missing_counts = {
        key: sum(not record["required_machine_metadata"][key] for record in records)
        for key in REQUIRED_METADATA
    }
    return {
        "schema": "fb.research_outcome_freeze.v1",
        "scope": "Every Markdown file named Result*.md directly under brain/07-Research; this freezes the note bytes only and does not reconstruct historical source data, code, costs, periods, exposure, or independent run identity.",
        "result_note_count": len(records),
        "required_metadata_fields": list(REQUIRED_METADATA),
        "result_notes_missing_required_metadata": missing_counts,
        "records": records,
    }


def render(root: Path = ROOT) -> str:
    return json.dumps(build_inventory(root), ensure_ascii=False, indent=2) + "\n"


def validate(root: Path = ROOT) -> list[str]:
    output = root / OUTPUT
    try:
        actual = output.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"cannot read research outcome freeze: {exc}"]
    expected = render(root)
    if actual != expected:
        return ["research outcome freeze is stale; regenerate it with scripts/research_outcome_freeze.py"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the checked-in inventory")
    args = parser.parse_args()
    output = ROOT / OUTPUT
    if args.check:
        errors = validate()
        if errors:
            for error in errors:
                print(f"FAIL {error}")
            return 1
        manifest = build_inventory()
        print(f"research outcome freeze current: {manifest['result_note_count']} result notes; content hashes and metadata gaps recorded")
        return 0
    output.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT.as_posix()} ({len(build_inventory()['records'])} result notes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
