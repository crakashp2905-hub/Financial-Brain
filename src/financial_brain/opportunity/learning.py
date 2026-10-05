"""What kinds of opportunity does the system keep rejecting that later win?

Opportunity memory records every candidate and why it was declined. This turns that record into the question a
learning system should ask: grouping the **rejections** by their reason and by what the names looked like (size,
volatility, momentum, each in quintiles within its own session), which groups went on to beat the universe - by
enough, and consistently enough across sessions, that the rule rejecting them is costing money?

A group is flagged only if its mean excess return clears ``T_FLAG`` with one observation per session (names on the
same session share a market), and the number of groups examined is printed beside the result, because examining
75 groups and flagging three at t = 2 is what chance looks like.

This produces *hypotheses* for a new pre-registered rule version. It changes nothing by itself.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

BUCKETS = (("size", "adv20"), ("volatility", "vol_60"), ("momentum", "mom_12_1"))
T_FLAG = 3.0
MIN_SESSIONS = 30


class LearningError(ValueError):
    pass


def frame(con, run_id: str) -> pd.DataFrame:
    df = con.execute("""
        SELECT m.session, m.lineage, m.disposition, COALESCE(m.reason, m.disposition) AS reason, m.fwd_excess,
               f.adv20, f.vol_60, f.mom_12_1
        FROM opportunity_memory m
        JOIN features f ON f.lineage = m.lineage AND f.business_date = m.session
        WHERE m.run_id = ? AND m.fwd_excess IS NOT NULL""", [run_id]).df()
    if df.empty:
        raise LearningError(f"no resolved memory for {run_id!r}")
    return df


def systematic_misses(df: pd.DataFrame, *, t_flag: float = T_FLAG, min_sessions: int = MIN_SESSIONS) -> dict:
    d = df.copy()
    for label, col in BUCKETS:
        d[label] = d.groupby("session")[col].transform(lambda s: pd.qcut(s.rank(method="first"), 5, labels=False) + 1)
    rejected = d[d["disposition"].str.startswith("REJECTED")]
    rows = []
    for label, _ in BUCKETS:
        for (reason, q), g in rejected.groupby(["reason", label]):
            per = g.groupby("session")["fwd_excess"].mean()
            if len(per) < min_sessions:
                continue
            t = float(per.mean() / (per.std(ddof=1) / np.sqrt(len(per)))) if per.std(ddof=1) > 0 else 0.0
            rows.append({"reason": reason, "dimension": label, "quintile": int(q), "names": int(len(g)),
                         "sessions": int(len(per)), "mean_excess": float(per.mean()), "t": t,
                         "flag": t >= t_flag})
    res = pd.DataFrame(rows).sort_values("t", ascending=False) if rows else pd.DataFrame()
    return {"groups_examined": int(len(res)), "flagged": res[res["flag"]] if len(res) else res, "all": res,
            "chance_note": f"{len(res)} groups at t >= {t_flag}: about {len(res) * 0.00135:.1f} would clear by chance"}
