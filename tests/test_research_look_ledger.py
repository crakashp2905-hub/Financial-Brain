"""Research look counting is exhaustive, append-only and assumption-sensitive."""
from datetime import datetime, timezone

import pytest

from financial_brain.research.look_ledger import ResearchLook, ResearchLookError, ResearchLookLedger

NOW = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)


def look(look_id="look-1", look_type="PARAMETER_VARIANT", assumption=None):
    return ResearchLook(look_id, "experiment-1", look_type, "attempted grid variant",
                        assumption or {"window": 20, "cost_bps": 15}, NOW)


def test_every_look_type_is_counted_and_identical_retry_is_idempotent(tmp_path):
    ledger = ResearchLookLedger(tmp_path / "looks.sqlite3")
    try:
        for index, kind in enumerate(("FEATURE", "INDICATOR", "PARAMETER_VARIANT", "HORIZON",
                                      "STOP_RULE", "TAKE_PROFIT_RULE", "UNIVERSE_FILTER",
                                      "TRADING_RULE", "COST_ASSUMPTION")):
            assert ledger.append(look(str(index), kind, {"attempt": index}))
        assert not ledger.append(look("0", "FEATURE", {"attempt": 0}))
        report = ledger.audit()
        assert report["look_count"] == 9
        assert all(count == 1 for count in report["counts_by_type"].values())
        assert report["integrity"] == "VALID"
    finally:
        ledger.close()


def test_changed_assumptions_require_new_look_and_hash_chain_detects_tampering(tmp_path):
    path = tmp_path / "looks.sqlite3"
    ledger = ResearchLookLedger(path)
    ledger.append(look())
    with pytest.raises(ResearchLookError, match="new look ID"):
        ledger.append(look(assumption={"window": 30, "cost_bps": 15}))
    ledger.close()
    import sqlite3
    with sqlite3.connect(path) as con:
        con.execute("UPDATE research_looks SET body_json='{}' WHERE look_id='look-1'")
    corrupt = ResearchLookLedger(path)
    try:
        with pytest.raises(ResearchLookError, match="corrupt"):
            corrupt.audit()
    finally:
        corrupt.close()


def test_family_snapshot_is_complete_content_hashed_and_changes_with_new_looks(tmp_path):
    ledger = ResearchLookLedger(tmp_path / "looks.sqlite3")
    try:
        ledger.append(look("candidate-a", "TRADING_RULE", {"rule": "momentum"}))
        ledger.append(look("candidate-b", "STOP_RULE", {"stop": 2.0}))
        first = ledger.family_snapshot("experiment-1")
        assert first["look_ids"] == ("candidate-a", "candidate-b")
        assert first["look_count"] == 2
        ledger.append(look("candidate-c", "COST_ASSUMPTION", {"fee": 0.001}))
        second = ledger.family_snapshot("experiment-1")
        assert second["look_ids"] == ("candidate-a", "candidate-b", "candidate-c")
        assert second["source_sha256"] != first["source_sha256"]
    finally:
        ledger.close()
