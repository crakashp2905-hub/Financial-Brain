"""How to hold momentum cheaply in India: H005 variants vs listed momentum indices (2026-11-18).

Descriptive implementation study, not a new strategy search, but logged as a look. Uses the BE/T-filled
panel (bet_recheck.py). All series are monthly net excess over the same equal-weight top-1000
universe benchmark hundred.py uses.

* H005 as registered (20 names, monthly)
* H005 with its 10 best names (monthly)
* H005 rebalanced quarterly (20 names)
* listed momentum indices, net of a 0.30%/yr fund expense (an index fund or ETF holding them)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import bet_recheck as BR  # noqa: E402
import hundred as H  # noqa: E402
import hundred_spec as SPEC  # noqa: E402
from financial_brain.evaluation import dynamic_standard as D  # noqa: E402
from financial_brain.evaluation import edges as E  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "docs/trials/dynamic/momentum-implementation-2026-11-18.json"
INDICES = ["Nifty200 Momentum 30", "Nifty500 Momentum 50", "Nifty Midcap150 Momentum 50",
           "Nifty MidSmallcap400 Momentum Quality 100", "Nifty Smallcap250 Momentum Quality 100"]
FEE_M = 0.0030 / 12


def summary(s: pd.Series, name: str) -> dict:
    s = s.dropna()
    g = D.judge(D.Spec(name, "A", SPEC.family_size("mom"), 1), s, null_pass=None)
    cum = (1 + s).cumprod()
    return {"months": int(len(s)), "mean_pct": round(100 * float(s.mean()), 3), "t": g.get("t_used"),
            "halves": [g.get("t_a"), g.get("t_b")], "tier": g.get("tier"),
            "worst_month_pct": round(100 * float(s.min()), 2),
            "max_drawdown_pct": round(100 * float((cum / cum.cummax() - 1).min()), 2)}


def main() -> None:
    E.load = BR._filled_load
    H.E.load = BR._filled_load
    X = H.Data()
    row = next(r for r in SPEC.C if r[0] == "H005")
    b = H.build(X, row)
    picks, dates, U = b["picks"], b["dates"], b["U"]
    out: dict[str, dict] = {}
    base = H.run_book(X, picks, dates, U)
    out["H005 registered (20, monthly)"] = summary(base["net_excess"], "H005")
    out["H005 registered (20, monthly)"]["mean_cost_pct"] = round(100 * float(base["cost"].mean()), 3)
    p10 = {d: v[:10] for d, v in picks.items()}
    b10 = H.run_book(X, p10, dates, U, min_names=5)
    out["H005 top 10 (monthly)"] = {**summary(b10["net_excess"], "H005-10"),
                                    "mean_cost_pct": round(100 * float(b10["cost"].mean()), 3)}
    q_dates = dates[::3]
    bq = H.run_book(X, {d: picks[d] for d in q_dates if d in picks}, q_dates, U)
    # quarterly periods: report per-quarter, and a per-month equivalent mean for comparison
    sq = summary(bq["net_excess"], "H005-Q")
    sq["note"] = "periods are quarters; mean_pct is per quarter"
    sq["mean_per_month_pct"] = round(sq["mean_pct"] / 3, 3)
    sq["mean_cost_pct_per_quarter"] = round(100 * float(bq["cost"].mean()), 3)
    out["H005 quarterly (20)"] = sq
    # listed momentum indices: the stored history (index_levels, PRICE only) has multi-year and
    # multi-week gaps up to 2026, so no fair monthly comparison is possible from it. Recorded as such;
    # the full daily TRI history from NSE Indices is needed first.
    lv = X.con.execute("SELECT index_name, COUNT(*) n, MIN(business_date) a, MAX(business_date) b FROM index_levels "
                       "WHERE variant = 'PRICE' AND index_name IN (" + ",".join("?" * len(INDICES)) + ") GROUP BY 1",
                       INDICES).df()
    out["listed momentum indices"] = {"status": "INSUFFICIENT_DATA",
                                      "stored_rows": {r["index_name"]: int(r["n"]) for _, r in lv.iterrows()},
                                      "need": "daily total-return history from NSE Indices"}
    OUT.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    for k, v in out.items():
        print(f"{k:55s} {v}")


if __name__ == "__main__":
    main()
