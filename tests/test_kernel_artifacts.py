"""Keeps the generated pilot ship-gate artifacts present, reproducible and honestly labelled."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_reconciliation_artifact_passes_and_is_synthetic_labelled() -> None:
    doc = json.loads((ROOT / "artifacts" / "accounting" / "reconciliation.json").read_text(encoding="utf-8"))
    assert doc["status"] == "PASS"
    assert doc["synthetic_only"] is True
    assert all(c["trial_balance_ok"] and c["cash_equals_net_pnl"] for c in doc["checks"])
    for field in ("schema_version", "code_sha", "generated_at", "environment", "fixture_sha256", "blockers"):
        assert field in doc


def test_trace_has_full_causal_chain() -> None:
    lines = (ROOT / "artifacts" / "replay" / "trade-trace.jsonl").read_text(encoding="utf-8").strip().splitlines()
    stages = [json.loads(line)["stage"] for line in lines]
    assert stages == ["opening_range", "signal", "gate", "risk", "fill", "exit", "ledger"]


def test_capabilities_do_not_claim_completion() -> None:
    doc = json.loads((ROOT / "artifacts" / "capabilities.json").read_text(encoding="utf-8"))
    assert doc["status"] == "PARTIAL"
    assert doc["total_tasks"] == 711
    assert "no real market data" in " ".join(doc["blockers"])
