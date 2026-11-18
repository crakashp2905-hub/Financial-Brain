"""One preregistered run of the Four-Node v3 family (docs/strategies/four_node_v3/PREREGISTRATION.md).

Usage: python scripts/research/four_node_v3_run.py <npz_dir> <out_dir>
"""
from __future__ import annotations

import json
import math
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from financial_brain.costs.india import CostModel, Segment
from financial_brain.strategies.four_node_v3.backtest import STRATEGIES, trades_for

PERIODS = (("DISCOVERY", "2016-01-01", "2021-12-31"), ("VALIDATION", "2022-01-01", "2023-12-31"),
           ("TEST", "2024-01-01", "2099-12-31"))
CM = CostModel()
RT_I = CM.round_trip(turnover=1e6, segment=Segment.INTRADAY, bucket="mega")["bps"] / 1e4
RT_D = CM.round_trip(turnover=1e6, segment=Segment.DELIVERY, bucket="mega")["bps"] / 1e4


def run_one(path: str) -> dict[str, list[dict]]:
    z = np.load(path, allow_pickle=True)
    days = [str(d) for d in z["days"]]
    return {name: [{"day": days[t.day], "reason": t.reason, "net": t.net, "r": t.r}
                   for t in trades_for(z["o"], z["h"], z["l"], z["c"], z["v"], cfg, RT_I, RT_D)]
            for name, cfg in STRATEGIES.items()}


def stats(x: list[dict], cost: float) -> dict:
    n = len(x)
    if n < 2:
        return {"n": n}
    a = np.array([t["net"] for t in x])
    m, s = float(a.mean()), float(a.std(ddof=1))
    t = m / (s / math.sqrt(n)) if s else 0.0
    ex: dict[str, int] = {}
    for r in x:
        ex[r["reason"]] = ex.get(r["reason"], 0) + 1
    return {"n": n, "mean_net_bps": m * 1e4, "gross_bps": (m + cost) * 1e4, "t": t,
            "p_one_sided": 0.5 * math.erfc(t / math.sqrt(2)), "win": float((a > 0).mean()),
            "mean_R": float(np.mean([r["r"] for r in x])), "exits": ex}


def main(src: Path, dst: Path) -> None:
    files = sorted(str(p) for p in src.glob("*.npz"))
    rows: dict[str, list[dict]] = {k: [] for k in STRATEGIES}
    with ProcessPoolExecutor(max_workers=7) as ex:
        for part in ex.map(run_one, files):
            for k, v in part.items():
                rows[k] += v
    res = {}
    for k, cfg in STRATEGIES.items():
        cost = RT_D if cfg.get("hold_days") else RT_I
        res[k] = {name: stats([r for r in rows[k] if a <= r["day"] <= b], cost) for name, a, b in PERIODS}
    fam = sorted((res[k]["VALIDATION"].get("p_one_sided", 1.0), k) for k in STRATEGIES if k != "CONTROL_v2")
    running = 0.0
    for i, (p, k) in enumerate(fam):
        running = max(running, min(1.0, (len(fam) - i) * p))
        res[k]["VALIDATION"]["p_holm"] = running
    for k in STRATEGIES:
        r = res[k]
        gates = [r["DISCOVERY"].get("mean_net_bps", -1) > 0,
                 r["VALIDATION"].get("mean_net_bps", -1) > 0 and r["VALIDATION"].get("p_holm", 1) < 0.05,
                 r["TEST"].get("mean_net_bps", -1) > 0 and r["TEST"].get("t", 0) >= 2,
                 all(r[p].get("n", 0) >= 100 for p in ("DISCOVERY", "VALIDATION", "TEST"))]
        r["gates"] = gates
        r["verdict"] = "CONTROL" if k == "CONTROL_v2" else ("PASS" if all(gates) else "FAIL")
    out = {"round_trip_intraday": RT_I, "round_trip_delivery": RT_D, "files": len(files), "strategies": res}
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "run.json").write_text(json.dumps(out, indent=1))
    for k, r in res.items():
        print(k, r["verdict"], *[(p, r[p].get("n"), round(r[p].get("mean_net_bps", 0), 1), round(r[p].get("t", 0), 2))
                                for p in ("DISCOVERY", "VALIDATION", "TEST")])


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
