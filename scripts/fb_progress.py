"""Progress and integrity check for the unified platform blueprint tracker (711 FB tasks).

Reads ``docs/audits/unified-platform-tracker.json`` and reports, per status, how many FB tasks are done.

Statuses: implemented (evidence test files exist on disk), partial, open, external_blocked.
``--check`` exits 1 when the tracker is malformed: missing or duplicate IDs, unknown statuses, an
``implemented`` row without existing evidence, or a row that claims evidence not on disk.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRACKER = ROOT / "docs" / "audits" / "unified-platform-tracker.json"
STATUSES = ("implemented", "partial", "open", "external_blocked")
EXPECTED_TOTAL = 711


def progress_bar(done: int, total: int, width: int = 30) -> str:
    if total <= 0:
        return f"[{'-' * width}] 0/0 (0.0%)"
    filled = round(width * done / total)
    return f"[{'#' * filled}{'-' * (width - filled)}] {done}/{total} ({100.0 * done / total:.1f}%)"


def validate(doc: dict) -> list[str]:
    problems: list[str] = []
    tasks = doc.get("tasks", [])
    ids = [t["id"] for t in tasks]
    if len(tasks) != EXPECTED_TOTAL:
        problems.append(f"expected {EXPECTED_TOTAL} tasks, found {len(tasks)}")
    if len(set(ids)) != len(ids):
        problems.append("duplicate task IDs")
    expected = {f"FB-{n:04d}" for n in range(1, EXPECTED_TOTAL + 1)}
    if set(ids) != expected:
        problems.append("task IDs are not exactly FB-0001..FB-0711")
    for t in tasks:
        if t["status"] not in STATUSES:
            problems.append(f"{t['id']}: unknown status {t['status']!r}")
        for ev in t.get("evidence", []):
            if not (ROOT / ev).is_file():
                problems.append(f"{t['id']}: evidence file missing: {ev}")
        if t["status"] == "implemented" and not t.get("evidence"):
            problems.append(f"{t['id']}: implemented without evidence")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 if the tracker is malformed")
    args = parser.parse_args(argv)
    doc = json.loads(TRACKER.read_text(encoding="utf-8"))
    problems = validate(doc)
    tasks = doc["tasks"]
    counts = Counter(t["status"] for t in tasks)
    done = counts["implemented"]
    print("Unified platform FB tasks")
    print(progress_bar(done, len(tasks)))
    for status in STATUSES:
        print(f"  {status:<17} {counts[status]}")
    per_ws: dict[str, Counter] = {}
    for t in tasks:
        per_ws.setdefault(t["workstream"], Counter())[t["status"]] += 1
    print("By workstream (implemented / total):")
    for ws in sorted(per_ws, key=lambda s: int(s[1:])):
        total = sum(per_ws[ws].values())
        print(f"  {ws:<4} {per_ws[ws]['implemented']:>2} / {total:<2}")
    if problems:
        print("PROBLEMS:")
        for p in problems:
            print(f"  - {p}")
    if args.check and problems:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
