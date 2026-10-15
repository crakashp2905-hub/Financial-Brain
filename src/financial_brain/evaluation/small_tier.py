"""ADR-0006's small-allocation tier: may a modest edge earn a small, owner-approved allocation?

A strategy is ``CANDIDATE_SMALL`` when, on its **registered** backtest series and its live paper record:

    1. the net mean is positive in both 2016-2021 and 2022-2026 (the halves G3 uses);
    2. it beat its G4 null (from the ADR-0004 trial ledger);
    3. the full-sample t is >= 2.5 (one test over the whole period);
    4. its live paper record has >= 40 sessions, and has never been below the backtest's 90% band at a month end.

Out-of-sample-only registrations (chosen after a result) have no ledger G4 and so never qualify - by design. The
thresholds were fixed in the proposal before anyone checked who would pass; they are not to be moved to admit a
strategy (ADR-0006).
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import oos_monitor as OM
from . import standard as S

MIN_FULL_T = 2.5
MIN_LIVE_SESSIONS = 40
CANDIDATE_SMALL = "CANDIDATE_SMALL"

#: Which ADR-0004 ledger entry holds each registered strategy's G4 result.
LEDGER_NAME = {"edge_m1_momentum_book": "M1 cost-aware momentum book", "carry_basis_10": "C1 rich-basis carry",
               "edge_f3_short_buildup": "F3 short build-up avoid filter"}


def _g4(strategy: str, ledger_path: Path = S.LEDGER) -> bool | None:
    name = LEDGER_NAME.get(strategy)
    if not name:
        return None
    for row in S.ledger(ledger_path):
        if row.get("name") == name and row.get("g4_stat") is not None:
            return bool(row["g4_stat"] > row["g4_null_q95"])
    return None


def assess(strategy: str, zoo_con=None, *, ledger_path: Path = S.LEDGER) -> dict:
    reg = {r.strategy: r for r in OM.REGISTERED}.get(strategy)
    if reg is None or reg.components or not Path(reg.backtest).exists():
        return {"strategy": strategy, "qualifies": False, "why": ["no registered backtest series"]}
    s = pd.read_parquet(reg.backtest)[reg.column]
    rep = S.replication(s)
    full_t = S.tstat(s)
    g4 = _g4(strategy, ledger_path)
    live = {"sessions": 0, "below_band_months": None}
    if zoo_con is not None:
        rows = zoo_con.execute("SELECT day, net, bench FROM zoo_pnl WHERE strategy = ? ORDER BY day", [strategy]).df()
        a = OM.assess(rows, s, reg)
        live = {"sessions": a.get("sessions", 0), "verdict": a.get("verdict"),
                "below_band_months": sum(1 for m in a.get("months", []) if m["where"] == "below")}
    checks = {
        "positive in both halves": bool(rep["mean_a"] > 0 and rep["mean_b"] > 0),
        "beat its G4 null": g4 is True,
        f"full-sample t >= {MIN_FULL_T}": bool(full_t >= MIN_FULL_T),
        f"live paper >= {MIN_LIVE_SESSIONS} sessions": live["sessions"] >= MIN_LIVE_SESSIONS,
        "never below the band at a month end": live.get("below_band_months") == 0,
    }
    return {"strategy": strategy, "qualifies": all(checks.values()), "checks": checks,
            "full_t": round(float(full_t), 2), "halves": (round(rep["mean_a"], 5), round(rep["mean_b"], 5)),
            "live": live, "why": [k for k, v in checks.items() if not v]}


def report(zoo_con=None) -> list[dict]:
    return [assess(r.strategy, zoo_con) for r in OM.REGISTERED if not r.components]


def as_json(rows) -> str:
    return json.dumps(rows, default=str, indent=1)
