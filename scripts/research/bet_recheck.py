"""Re-check the three momentum books with BE/T-series prices filled in (2026-11-18).

NSE moves stocks into the BE/T trade-for-trade series under surveillance, often right after big
rallies. ``adjusted_prices`` holds EQ-type rows only, so those days are missing (about 1,872 ISINs).
This script fills them into the close panel the registered study used, then reruns P3, H003 and H005
exactly as registered (hundred.py construction and grading), with and without the fill.

Fill rule: close_adj = BE/T close_raw x the lineage's adjustment ratio (close_adj / close_raw) from
the nearest EQ day, forward-filled, else back-filled. The universe mask is left as it was (a name is
not newly selected while it has no EQ features row); only prices change, so held names earn their
real BE/T-period return and momentum scores see those prices.

Not a retune: the specs are unchanged. Logged as a look in docs/trials/dynamic/ledger.jsonl.
Run from the main checkout: python <this file> (reads data/financial_brain.duckdb read-only).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import hundred as H  # noqa: E402
import hundred_spec as SPEC  # noqa: E402
from financial_brain.evaluation import dynamic_standard as D  # noqa: E402
from financial_brain.evaluation import edges as E  # noqa: E402

ROWS = [("P3", "frog-in-the-pan momentum, top 20 of 500 (P3 as registered)", "mom", "A", 1, "xs", "fip",
         {"lo": 1, "hi": 500})] + [r for r in SPEC.C if r[0] in ("H003", "H005")]
OUT = Path(__file__).resolve().parents[2] / "docs/trials/dynamic/bet-recheck-2026-11-18.json"
_orig_load = E.load
FILLED = {"cells": 0}


def _filled_load(rcon, start="2015-01-01", n=500):
    P = _orig_load(rcon, start=start, n=n)
    C = P["C"]
    rcon.register("_lin", pd.DataFrame({"lineage": list(C.columns)}))
    isin = rcon.execute("""SELECT DISTINCT lineage, isin FROM adjusted_prices JOIN _lin USING (lineage)""").df()
    rcon.register("_isin", isin)
    be = rcon.execute("""SELECT i.lineage, e.trade_date AS d, any_value(e.close_price) AS c
                         FROM eod_prices e JOIN _isin i USING (isin)
                         WHERE e.series IN ('BE', 'T') AND e.close_price > 0 AND e.trade_date >= ?
                         GROUP BY 1, 2""", [start]).df()
    ratio = rcon.execute("""SELECT lineage, business_date AS d, close_adj / close_raw AS k
                            FROM adjusted_prices JOIN _lin USING (lineage) WHERE close_raw > 0""").df()
    be["d"], ratio["d"] = pd.to_datetime(be["d"]), pd.to_datetime(ratio["d"])
    K = ratio.pivot_table(index="d", columns="lineage", values="k").reindex(C.index.union(be["d"].unique()))
    K = K.sort_index().ffill().bfill()
    B = be.pivot_table(index="d", columns="lineage", values="c").reindex(index=C.index, columns=C.columns)
    adj = B * K.reindex(index=C.index, columns=C.columns)
    fill = C.isna() & adj.notna()
    FILLED["cells"] = int(fill.to_numpy().sum())
    P["C"] = C.where(~fill, adj)
    return P


def run(label: str) -> dict:
    X = H.Data()
    out = {}
    for row in ROWS:
        cid, name, fam, prior, direction, engine, key, p = row
        b = H.build(X, row)
        bk = H.run_book(X, b["picks"], b["dates"], b["U"])
        s = bk["net_excess"]
        g = D.judge(D.Spec(name, prior, SPEC.family_size(fam), direction), s, null_pass=None)
        out[cid] = {"mean_pct": float(100 * s.mean()), "t": g.get("t_used"), "halves": [g.get("t_a"), g.get("t_b")],
                    "posterior": g.get("posterior"), "tier": g.get("tier"), "n": g.get("n"),
                    "worst_month_pct": float(100 * s.min())}
        print(label, cid, out[cid], flush=True)
    return out


def main() -> None:
    base = run("EQ-only")
    E.load = _filled_load
    H.E.load = _filled_load
    filled = run("EQ+BE/T")
    res = {"date": "2026-11-18", "filled_cells": FILLED["cells"], "eq_only": base, "eq_plus_be_t": filled,
           "note": "null test not rerun; tiers are before the random-book null"}
    OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print("filled cells", FILLED["cells"])


if __name__ == "__main__":
    main()
