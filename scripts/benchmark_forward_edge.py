"""Measure journal event ingest/recovery on a caller-supplied local JSONL dataset."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Any

from financial_brain.forward_edge.durable import DurableJournal


def _rss_bytes() -> int | None:
    try:
        import psutil
        return int(psutil.Process().memory_info().rss)
    except (ImportError, OSError):
        return None


def benchmark(dataset: Path, report_json: Path | None = None) -> dict[str, Any]:
    source = Path(dataset)
    raw = source.read_bytes()
    rows = []
    for line_no, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"invalid JSON at line {line_no}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"dataset row {line_no} must be a JSON object")
        rows.append(record)
    if not rows:
        raise ValueError("benchmark dataset must contain at least one JSON record")
    rss_before = _rss_bytes()
    tracemalloc.start()
    append_ms = []
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="fb-pe0202-") as temp:
        journal_path = Path(temp) / "benchmark.jsonl"
        journal = DurableJournal(journal_path)
        for row in rows:
            tick = time.perf_counter()
            journal.append(row)
            append_ms.append((time.perf_counter() - tick) * 1000)
        ingest_seconds = time.perf_counter() - started
        written_bytes = journal_path.stat().st_size
        recover_started = time.perf_counter()
        recovered = DurableJournal(journal_path)
        recovery_seconds = time.perf_counter() - recover_started
        if recovered.seq != len(rows) or recovered.head != journal.head:
            raise RuntimeError("journal recovery differs from the just-written event stream")
    _, python_peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = _rss_bytes()
    report_seconds = None
    report_sha256 = None
    if report_json is not None:
        from financial_brain.forward_edge.daily_report import render_daily_reports, validate_daily_report
        report = json.loads(Path(report_json).read_text(encoding="utf-8"))
        validate_daily_report(report)
        report_started = time.perf_counter()
        rendered = render_daily_reports(report)
        report_seconds = time.perf_counter() - report_started
        report_sha256 = hashlib.sha256(b"".join(rendered[name] for name in sorted(rendered))).hexdigest()
    elapsed = max(ingest_seconds, 1e-12)
    return {
        "schema_version": "fb.forward-edge-benchmark.v1",
        "dataset": {"name": source.name, "sha256": hashlib.sha256(raw).hexdigest(),
                    "bytes": len(raw), "events": len(rows), "classification": "UNATTESTED_LOCAL_INPUT"},
        "ingest": {"seconds": ingest_seconds, "events_per_second": len(rows) / elapsed,
                   "journal_bytes": written_bytes, "disk_write_bytes_per_second": written_bytes / elapsed,
                   "append_latency_ms_p50": statistics.median(append_ms),
                   "append_latency_ms_p95": sorted(append_ms)[max(0, int(0.95 * len(append_ms)) - 1)]},
        "recovery": {"seconds": recovery_seconds, "events_per_second": len(rows) / max(recovery_seconds, 1e-12)},
        "memory": {"process_rss_before_bytes": rss_before, "process_rss_after_bytes": rss_after,
                   "python_tracemalloc_peak_bytes": python_peak_bytes},
        "report": {"seconds": report_seconds, "rendered_outputs_sha256": report_sha256},
        "limits": {"status": "NOT_SET", "reason": "Requires representative local workload and owner-approved SLOs."},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="local JSONL durable event records")
    parser.add_argument("--report-json", type=Path, help="validated daily report JSON to render")
    parser.add_argument("--output", type=Path, required=True, help="benchmark result JSON artifact")
    args = parser.parse_args()
    result = benchmark(args.dataset, args.report_json)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"events": result["dataset"]["events"],
                      "events_per_second": round(result["ingest"]["events_per_second"], 2),
                      "limits": "NOT_SET", "dataset_sha256": result["dataset"]["sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
