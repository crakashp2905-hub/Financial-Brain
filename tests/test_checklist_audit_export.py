from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import export_checklist_audit as audit


def test_published_audit_has_all_checklist_items_and_required_fields() -> None:
    audit.validate()
    rows = [json.loads(line) for line in audit.AUDIT.read_text(encoding="utf-8").splitlines()]
    status = json.loads(audit.STATUS.read_text(encoding="utf-8"))
    assert len(rows) == status["total_items"] == 1_378
    assert [row["id"] for row in rows] == [item["id"] for item in status["items"]]
    required = {"id", "status", "source", "test", "artifact_hash", "blocker", "commit"}
    assert all(required.issubset(row) for row in rows)
    assert all(row["source"]["path"] == audit.SOURCE.relative_to(audit.ROOT).as_posix() for row in rows)
    assert all(row["source"]["line"] > 0 for row in rows)
    assert any(row["status"] == "UNASSESSED" and row["test"] == [] for row in rows)


def test_check_rejects_stale_or_tampered_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stale = tmp_path / "audit.jsonl"
    stale.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(audit, "AUDIT", stale)
    with pytest.raises(ValueError, match="stale"):
        audit.validate()
