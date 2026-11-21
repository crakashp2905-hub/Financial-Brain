"""The momentum family's monthly books as NET EXCESS over the same universe, with the random-book null.

``data/edges/P0.parquet`` and ``P3.parquet`` hold net *total* return (the registered unit for the Sharpe
comparison), so their t-statistics are mostly market return. This recomputes the registered books unchanged and
reports ``net_excess`` (gross - cost - the equal-weight top-500 over the same month), then grades them under
ADR-0007. Read-only on the research database.

    python scripts/research/momentum_excess.py     -> docs/trials/dynamic/momentum-excess-2026-11-16.json
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from financial_brain.evaluation import dynamic_standard as D
from financial_brain.evaluation import edges as E
from financial_brain.evaluation import standard as S

OUT = Path("docs/trials/dynamic")
DRAWS = 200


def main() -> None:
    con = duckdb.connect("data/financial_brain.duckdb", read_only=True)
    P = E.load(con)
    mom = E.momentum_scores(P)
    idd = E.info_discreteness(P)
    uni = P["U"].reindex(idd.index)
    med = idd.where(uni).median(axis=1)
    low = idd.le(med, axis=0)
    books = {"P0 plain momentum": E.top_picks(P, mom), "P3 frog in the pan": E.top_picks(P, mom, mask=low)}
    # A holding with no price at the next month end (suspended, delisted, merged) is scored at its last traded
    # price - the project's stated survivorship policy (HANDOFF, 2026-09-19). Scores above use the raw panel.
    P = {**P, "C": P["C"].ffill()}
    eligible = {d: list(P["U"].loc[d][P["U"].loc[d]].index.intersection(mom.loc[d].dropna().index))
                for d in books["P0 plain momentum"]}
    res, series = {}, {}
    for name, picks in books.items():
        sel = E.monthly_selection(P, picks)
        ex = sel["net_excess"]
        series[name] = ex
        real = S.replication(ex)["stat"]
        full = S.tstat(ex)
        sims_stat, sims_full = [], []
        for i in range(DRAWS):
            rb = S.random_books({d: picks[d] for d in sel.index.union(pd.Index([max(picks)]))
                                 if d in picks}, eligible, seed=20261116 + i)
            try:
                rs = E.monthly_selection(P, rb)["net_excess"]
            except ValueError:
                continue
            sims_stat.append(S.replication(rs)["stat"])
            sims_full.append(S.tstat(rs))
        q_full = float(np.quantile(sims_full, 0.95))
        null_pass = bool(full > q_full)
        g = D.judge(D.Spec(name, "A", 20), ex, null_pass=null_pass)
        turnover = float(np.mean([1 - len(set(picks[a]) & set(picks[b])) / max(len(picks[b]), 1)
                                  for a, b in zip(sorted(picks)[:-1], sorted(picks)[1:])]))
        res[name] = {"mean_net_excess_month": float(ex.mean()), "mean_cost_month": float(sel["cost"].mean()),
                     "mean_gross_excess_month": float((sel["gross"] - sel["bench"]).mean()),
                     "turnover_month": turnover, "t_full": full, "g3_stat": real,
                     "null_q95_full_t": q_full, "null_mean_full_t": float(np.mean(sims_full)), "null_draws": len(sims_full),
                     "null_pass": null_pass, "dynamic": g,
                     "by_year": {str(y): float(v) for y, v in ex.groupby(ex.index.year).sum().items()}}
        print(name, json.dumps({k: v for k, v in res[name].items() if k not in ("dynamic", "by_year")}, default=float))
        print("   ", {k: g[k] for k in ("t_iid", "t_hac", "z_adj", "t_a", "t_b", "posterior", "tier", "sharpe",
                                        "sharpe_shrunk", "capital_fraction", "why")})
        print("   ", res[name]["by_year"])
    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(series).to_parquet(OUT / "momentum-excess-series.parquet")
    (OUT / "momentum-excess-2026-11-16.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
