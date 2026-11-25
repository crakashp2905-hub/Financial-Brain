from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from scripts.profit_readiness import _canonical_text_bytes, build, validate


ROOT = Path(__file__).resolve().parents[1]


def test_canonical_text_hash_input_normalizes_windows_line_endings(tmp_path) -> None:
    lf = tmp_path / "lf.txt"
    crlf = tmp_path / "crlf.txt"
    lf.write_bytes(b"first\nsecond\n")
    crlf.write_bytes(b"first\r\nsecond\r\n")
    assert _canonical_text_bytes(lf) == _canonical_text_bytes(crlf)


def test_readiness_is_derived_from_canonical_ledger_and_bound_to_evidence() -> None:
    report = build(ROOT)
    status = json.loads((ROOT / "docs/edge-program/v3.0-status.json").read_text(encoding="utf-8"))
    assert report["counts_by_status"] == status["counts_by_status"]
    assert report["verified_complete_count"] == status["counts_by_status"]["COMPLETE"]
    assert report["assessed_count"] == status["assessment_summary"]["audited_count"]
    assert "completion_percentage" not in report
    assert report["overall_status"] in {"RED", "AMBER", "GREEN"}
    assert all(gate["evidence"] for gate in report["gates"])
    assert all("status" in item for gate in report["gates"] for item in gate["evidence"]
               if "item_id" in item)
    assert report["deployment"]["live_trading_authorized"] is False


def test_artifact_validator_rejects_hand_edited_counts_and_gate_states(tmp_path) -> None:
    target = tmp_path / "profit-readiness.json"
    report = build(ROOT)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    validate(ROOT, target)

    edited = dict(report)
    edited["verified_complete_count"] = report["verified_complete_count"] + 1
    target.write_text(json.dumps(edited, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="stale or hand-edited"):
        validate(ROOT, target)


def test_canonical_status_ledger_hand_edits_fail_before_report_generation(tmp_path) -> None:
    docs = tmp_path / "docs/edge-program"
    docs.mkdir(parents=True)
    for name in ("v3.0-status.json", "v3.0-audit.jsonl"):
        shutil.copy2(ROOT / "docs/edge-program" / name, docs / name)
    for relative in ("src/financial_brain/execution/order_policy.py",
                     "src/financial_brain/forward_edge/modes.py"):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    status_path = docs / "v3.0-status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["counts_by_status"]["COMPLETE"] += 1
    status_path.write_text(json.dumps(status), encoding="utf-8")
    with pytest.raises(ValueError, match="counts are stale or hand-edited"):
        build(tmp_path)
