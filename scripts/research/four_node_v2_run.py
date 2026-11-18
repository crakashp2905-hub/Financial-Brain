"""One preregistered run of FourNode_India_v2 (docs/strategies/four_node_v2/PREREGISTRATION.md).

Usage: python scripts/research/four_node_v2_run.py <npz_dir> <out_dir>
"""
from __future__ import annotations

import json
import math
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from financial_brain.costs.india import CostModel, Segment
from financial_brain.strategies.four_node_v2.backtest import CHOICES, PARAMS, trades_for

PERIODS = (("DISCOVERY", "2016-01-01", "2021-12-31"), ("VALIDATION", "2022-01-01", "2023-12-31"),
           ("TEST", "2024-01-01", "2099-12-31"))
RT = CostModel().round_trip(turnover=1_000_000, segment=Segment.INTRADAY, bucket="mega")["bps"] / 1e4


def run_one(path: str) -> list[dict]:
    z = np.load(path, allow_pickle=True)
    days = [str(d) for d in z["days"]]
    return [{"sym": Path(path).stem, "day": days[t.day], "side": t.side, "reason": t.reason,
             "net": t.net, "r": t.r} for t in trades_for(z["o"], z["h"], z["l"], z["c"], RT)]


def stats(x: list[dict]) -> dict:
    n = len(x)
    if n < 2:
        return {"n": n}
    a = np.array([t["net"] for t in x])
    m, s = float(a.mean()), float(a.std(ddof=1))
    t = m / (s / math.sqrt(n)) if s else 0.0
    reasons: dict[str, int] = {}
    for r in x:
        reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
    return {"n": n, "mean_net_bps": m * 1e4, "t": t, "win": float((a > 0).mean()),
            "mean_R": float(np.mean([r["r"] for r in x])), "gross_bps": (m + RT) * 1e4,
            "exits": reasons, "days": len({r["day"] for r in x})}


def main(src: Path, dst: Path) -> None:
    files = sorted(str(p) for p in src.glob("*.npz"))
    rows: list[dict] = []
    with ProcessPoolExecutor(max_workers=7) as ex:
        for part in ex.map(run_one, files):
            rows += part
    res = {name: stats([r for r in rows if a <= r["day"] <= b]) for name, a, b in PERIODS}
    ok = [res["DISCOVERY"].get("mean_net_bps", -1) > 0,
          res["VALIDATION"].get("mean_net_bps", -1) > 0 and res["VALIDATION"].get("t", 0) >= 2,
          res["TEST"].get("mean_net_bps", -1) > 0 and res["TEST"].get("t", 0) >= 2,
          all(res[p].get("n", 0) >= 100 for p in res)]
    out = {"strategy": "FourNode_India_v2", "choices": CHOICES, "params": PARAMS, "round_trip_cost": RT,
           "files": len(files), "periods": res, "gates": ok, "verdict": "PASS" if all(ok) else "FAIL"}
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "run.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
