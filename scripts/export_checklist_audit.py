"""Build/check the item-by-item v3 checklist audit export."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / "docs/edge-program/v3.0-status.json"
SOURCE = ROOT / "docs/edge-program/Financial_Brain_Unified_Master_Checklist_v3_0.md"
AUDIT = ROOT / "docs/edge-program/v3.0-audit.jsonl"
TASK_LINE_PATTERN = re.compile(
    r"^\s*-\s+\[[ xX]\]\s+\*\*((?:FB-\d{4})|(?:PA-\d{2}-\d{2})|(?:SL-\d{2}-\d{2}))\*\*\s+(.*)$"
)
STATUSES = {"UNASSESSED", "NOT_STARTED", "PARTIAL", "COMPLETE", "EXTERNAL_BLOCKED"}


def render_rows(status: Mapping[str, Any], source_text: str) -> list[dict[str, Any]]:
    lines_by_id: dict[str, tuple[int, str]] = {}
    for number, line in enumerate(source_text.splitlines(), start=1):
        match = TASK_LINE_PATTERN.match(line)
        if match:
            item_id, task_text = match.groups()
            if item_id in lines_by_id:
                raise ValueError(f"duplicate source checklist ID: {item_id}")
            lines_by_id[item_id] = (number, task_text)

    items = status.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("status ledger has no checklist items")
    item_ids = [item.get("id") for item in items]
    if len(item_ids) != len(set(item_ids)):
        raise ValueError("status ledger contains duplicate checklist IDs")
    if set(item_ids) != set(lines_by_id):
        missing = sorted(set(item_ids) - set(lines_by_id))
        extra = sorted(set(lines_by_id) - set(item_ids))
        raise ValueError(f"source/ledger IDs differ; missing_source={missing[:5]}, extra_source={extra[:5]}")

    rows: list[dict[str, Any]] = []
    for item in items:
        status_value = item.get("status")
        if status_value not in STATUSES:
            raise ValueError(f"invalid status for {item.get('id')}: {status_value!r}")
        source_line, source_text_for_item = lines_by_id[item["id"]]
        if item.get("text") != source_text_for_item:
            raise ValueError(f"status-ledger source text differs from checklist for {item['id']}")
        evidence = item.get("evidence", [])
        tests = item.get("test_evidence", [])
        rows.append({
            "id": item["id"],
            "status": status_value,
            "source": {
                "path": SOURCE.relative_to(ROOT).as_posix(),
                "line": source_line,
                "text": source_text_for_item,
            },
            "test": tests if tests else ([item["test_result"]] if item.get("test_result") else []),
            "artifact_hash": item.get("artifact_hash") or item.get("artifact_sha256"),
            "blocker": item.get("blocker"),
            "commit": item.get("commit"),
            "evidence": evidence,
        })
    return rows


def rendered_text() -> str:
    status = json.loads(STATUS.read_text(encoding="utf-8"))
    source_text = SOURCE.read_text(encoding="utf-8")
    source_hash = hashlib.sha256(source_text.replace("\r\n", "\n").encode("utf-8")).hexdigest()
    if source_hash != status.get("source_sha256"):
        raise ValueError("source checklist hash does not match the status ledger")
    rows = render_rows(status, source_text)
    observed_counts = Counter(item["status"] for item in status["items"])
    expected_counts = {key: observed_counts.get(key, 0) for key in sorted(STATUSES)}
    if status.get("counts_by_status") != expected_counts:
        raise ValueError("status ledger counts_by_status does not match item statuses")
    summary = status.get("assessment_summary", {})
    audited_items = [item for item in status["items"] if item["status"] != "UNASSESSED"]
    audited_ids = [item["id"] for item in audited_items]
    if summary.get("audited_ids") != audited_ids:
        raise ValueError("assessment summary audited_ids does not match assessed items")
    if summary.get("audited_count") != len(audited_items):
        raise ValueError("assessment summary audited_count does not match item statuses")
    if summary.get("verified_complete_count") != observed_counts.get("COMPLETE", 0):
        raise ValueError("assessment summary verified_complete_count does not match item statuses")
    if summary.get("status_counts") != expected_counts:
        raise ValueError("assessment summary status_counts does not match item statuses")
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)


def validate() -> None:
    expected = rendered_text()
    actual = AUDIT.read_text(encoding="utf-8")
    if actual != expected:
        raise ValueError("checklist audit export is stale; regenerate it with scripts/export_checklist_audit.py")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify rather than write the audit export")
    args = parser.parse_args()
    expected = rendered_text()
    if args.check:
        validate()
        print("v3 checklist audit export is current")
    else:
        AUDIT.write_text(expected, encoding="utf-8", newline="\n")
        print(f"wrote {AUDIT.relative_to(ROOT).as_posix()} ({len(expected.splitlines())} records)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
