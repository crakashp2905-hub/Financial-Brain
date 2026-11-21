import json
from pathlib import Path

from scripts.validate_market_data_sources import validate


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs/edge-program/market-data-sources.v1.json"


def test_runtime_source_alias_is_accepted_and_maps_to_kite_event_source():
    report = json.loads(MATRIX.read_text(encoding="utf-8"))
    kite = next(row for row in report["sources"] if row["id"] == "kite_connect_quotes_history_instruments")
    assert kite["runtime_source_id"] == "KITE_CONNECT_READ_ONLY"
    assert validate(MATRIX, ROOT) == []


def test_duplicate_or_empty_runtime_source_alias_is_rejected(tmp_path):
    report = json.loads(MATRIX.read_text(encoding="utf-8"))
    report["sources"][1]["runtime_source_id"] = "KITE_CONNECT_READ_ONLY"
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(json.dumps(report), encoding="utf-8")
    assert any("duplicate runtime_source_id" in error for error in validate(duplicate, ROOT))

    report["sources"][1]["runtime_source_id"] = " "
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps(report), encoding="utf-8")
    assert any("runtime_source_id must be a nonempty string" in error
               for error in validate(empty, ROOT))
