from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.benchmark_forward_edge import benchmark


def test_benchmark_records_dataset_identity_and_unattested_limits(tmp_path: Path) -> None:
    dataset = tmp_path / "events.jsonl"
    dataset.write_text("\n".join(json.dumps({"t": "MARKET_EVENT", "seq": n}) for n in range(1, 5)) + "\n",
                       encoding="utf-8")

    report = benchmark(dataset)

    assert report["dataset"]["events"] == 4
    assert report["dataset"]["classification"] == "UNATTESTED_LOCAL_INPUT"
    assert report["dataset"]["sha256"]
    assert report["ingest"]["events_per_second"] > 0
    assert report["ingest"]["disk_write_bytes_per_second"] > 0
    assert report["recovery"]["events_per_second"] > 0
    assert report["memory"]["python_tracemalloc_peak_bytes"] > 0
    assert report["report"]["seconds"] is None
    assert report["limits"]["status"] == "NOT_SET"


def test_benchmark_rejects_malformed_or_empty_dataset(tmp_path: Path) -> None:
    dataset = tmp_path / "bad.jsonl"
    dataset.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON object"):
        benchmark(dataset)
    dataset.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="at least one"):
        benchmark(dataset)
