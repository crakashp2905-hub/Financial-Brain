import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_strategy_inventory_is_deterministic_unique_and_source_hashed():
    inventory = json.loads((ROOT / "docs/edge-program/strategy-variant-inventory.json").read_text(encoding="utf-8"))
    items = inventory["items"]
    keys = [(row["family"], row["name"], row["version"], row["asset"], row["clock"]) for row in items]
    assert len(keys) == len(set(keys)) == inventory["candidate_count"]
    assert set(inventory["category_counts"]) >= {
        "historical_trial", "paper_zoo", "technical_search", "chart_pattern",
        "options", "futures", "ranking_portfolio", "python_strategy_module",
    }
    for row in items:
        raw = (ROOT / row["source"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row["source_sha256"]
        assert len(row["variant_sha256"]) == 64


def test_committed_strategy_inventory_is_current():
    result = subprocess.run(
        [sys.executable, "scripts/strategy_variant_inventory.py", "--check"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
