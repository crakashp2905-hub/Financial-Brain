# PE-0202 performance benchmark

Run the harness against a representative, local, newline-delimited event dataset:

```powershell
python scripts/benchmark_forward_edge.py .\artifacts\session-events.jsonl --report-json .\artifacts\daily-report.json --output .\artifacts\benchmark.json
```

The result binds the input dataset hash and records per-record durable append latency, event throughput, journal write throughput, recovery throughput/time, process RSS where `psutil` is installed, Python allocation peak, and daily report render latency when a validated report is supplied. The event dataset is treated as `UNATTESTED_LOCAL_INPUT`; the harness does not label or promote data.

The repository's `data/lake` and `data/curated` directories contain no local dataset files in the inspected checkout. The harness was tested with small synthetic records only. No capacity limit, alert threshold, or production latency claim is set; those require measurement on a representative owner-provided dataset and target machine.
