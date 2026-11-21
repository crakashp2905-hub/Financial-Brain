"""Benchmark the Python detector path to decide whether a native kernel is justified (CP-0311..0312, CP-0320).

Predeclared rule (written before measuring): a native (Rust) kernel is considered only if a
full-universe scan projects above ``BUDGET_SECONDS`` on this machine. The result is written to
docs/audits/pattern-benchmark.json. The decision is recorded, not assumed.
"""

from __future__ import annotations

import cProfile
import io
import json
import platform
import pstats
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from financial_brain.patterns.demo import synthetic_corpus  # noqa: E402
from financial_brain.patterns.detectors import discover  # noqa: E402

BUDGET_SECONDS = 600.0  # predeclared: 500 symbols x 1500 bars must finish within 10 minutes
UNIVERSE = 500
BARS = 1500
OUT = ROOT / "docs" / "audits" / "pattern-benchmark.json"


def main() -> int:
    corpus = synthetic_corpus(n=BARS, symbols=2)
    bars = next(iter(corpus.values()))
    start = time.perf_counter()
    discover(bars)
    per_symbol = time.perf_counter() - start

    profiler = cProfile.Profile()
    profiler.enable()
    discover(bars)
    profiler.disable()
    buf = io.StringIO()
    pstats.Stats(profiler, stream=buf).sort_stats("cumulative").print_stats(5)
    hot = [line.strip() for line in buf.getvalue().splitlines() if "detectors.py" in line][:3]

    projected = per_symbol * UNIVERSE
    decision = "native_kernel_justified" if projected > BUDGET_SECONDS else "python_sufficient"
    result = {
        "bars_per_symbol": BARS,
        "universe_symbols": UNIVERSE,
        "per_symbol_seconds": round(per_symbol, 4),
        "projected_full_scan_seconds": round(projected, 2),
        "budget_seconds": BUDGET_SECONDS,
        "decision": decision,
        "hotspots": hot,
        "python": platform.python_version(),
        "platform": sys.platform,
    }
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
