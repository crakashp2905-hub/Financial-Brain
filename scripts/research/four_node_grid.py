"""Run all 128 Four-Node interpretation variants over the 5-minute NSE stock bars.

Usage: python scripts/research/four_node_grid.py <npz_dir> <out_dir> [--limit N]

Periods (fixed before any result was seen):
  DISCOVERY  2016-01-01 .. 2021-12-31  ranking happens here only
  VALIDATION 2022-01-01 .. 2023-12-31  checks the discovery ranking
  TEST       2024-01-01 .. end         read once, for the variants chosen on DISCOVERY+VALIDATION
"""
from __future__ import annotations

import json
import math
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from financial_brain.costs.india import CostModel, Segment
from financial_brain.strategies.four_node.backtest import all_variants, events_for, outcome, takes, \
    variant_code

PERIODS = (("DISCOVERY", "2016-01-01", "2021-12-31"), ("VALIDATION", "2022-01-01", "2023-12-31"),
           ("TEST", "2024-01-01", "2099-12-31"))
RT = CostModel().round_trip(turnover=1_000_000, segment=Segment.INTRADAY, bucket="mega")["bps"] / 1e4


def _period(day: str) -> str:
    for name, a, b in PERIODS:
        if a <= day <= b:
            return name
    return "NONE"


def run_one(path: str) -> dict[str, list[tuple[str, str, float, float]]]:
    z = np.load(path, allow_pickle=True)
    days = [str(d) for d in z["days"]]
    evs = events_for(z["o"], z["h"], z["l"], z["c"], RT)
    sym = Path(path).stem
    out: dict[str, list[tuple[str, str, float, float]]] = {}
    for v in all_variants():
        code = variant_code(v)
        out[code] = [(sym, days[e.day], *outcome(e, v)) for e in evs if takes(e, v)]
    return out


def stats(rows: list[tuple[str, str, float, float]]) -> dict:
    n = len(rows)
    if n < 2:
        return {"n": n}
    x = np.array([r[2] for r in rows])
    rr = np.array([r[3] for r in rows])
    m, s = float(x.mean()), float(x.std(ddof=1))
    t = m / (s / math.sqrt(n)) if s > 0 else 0.0
    p = 0.5 * math.erfc(t / math.sqrt(2))       # one-sided, H1: mean > 0
    return {"n": n, "mean_net_bps": m * 1e4, "t": t, "p_one_sided": p, "win": float((x > 0).mean()),
            "mean_R": float(rr.mean()), "total_net": float(x.sum())}


def main(src: Path, dst: Path, limit: int | None) -> None:
    files = sorted(str(p) for p in src.glob("*.npz"))[:limit]
    merged: dict[str, list] = {variant_code(v): [] for v in all_variants()}
    with ProcessPoolExecutor(max_workers=7) as ex:
        for i, part in enumerate(ex.map(run_one, files, chunksize=1)):
            for k, rows in part.items():
                merged[k] += rows
            print(f"{i + 1}/{len(files)}", flush=True)
    variants = {variant_code(v): v for v in all_variants()}
    table = []
    for code, rows in merged.items():
        by = {name: [r for r in rows if _period(r[1]) == name] for name, _, _ in PERIODS}
        table.append({"code": code, "choices": variants[code],
                      **{name: stats(by[name]) for name in by}})
    # Holm correction across all 128 on DISCOVERY
    ps = sorted((t["DISCOVERY"].get("p_one_sided", 1.0), t["code"]) for t in table)
    m = len(ps)
    holm, running = {}, 0.0
    for i, (p, code) in enumerate(ps):
        running = max(running, min(1.0, (m - i) * p))
        holm[code] = running
    for t in table:
        t["DISCOVERY"]["p_holm"] = holm[t["code"]]
    table.sort(key=lambda t: -t["DISCOVERY"].get("t", -99))
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "grid.json").write_text(json.dumps({"round_trip_cost": RT, "files": len(files),
                                               "periods": PERIODS, "variants": table}, indent=1))
    print("written", dst / "grid.json")


if __name__ == "__main__":
    lim = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    main(Path(sys.argv[1]), Path(sys.argv[2]), lim)
