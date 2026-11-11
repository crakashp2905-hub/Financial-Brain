"""The loss miner: a pattern is a lesson only if it repeats on sessions it was not found on, and nothing it finds is
ever applied."""
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from financial_brain.paper import loss_miner as LM
from financial_brain.paper import loss_review as LR


def _trades(n_sessions: int = 60, seed: int = 1, *, real: bool = True, late_only: bool = False) -> pd.DataFrame:
    """Ten trades a session. ``real``: trades against the market lose ~40 bp in every session. ``late_only``: trades
    entered 10:30-11:00 lose 60 bp in the first 60% of sessions and make 20 bp after (a pattern found by luck)."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2026-10-01", periods=n_sessions).strftime("%Y-%m-%d")
    rows = []
    for k, day in enumerate(days):
        mkt = rng.choice([-0.004, 0.004])
        for i in range(10):
            side = "long" if i % 2 == 0 else "short"
            against = (mkt > 0) != (side == "long")
            minute = (570, 600, 650)[i % 3]
            bp = rng.normal(0, 30) + (-40 if (real and against) else 5)
            if late_only:
                bp = rng.normal(0, 30) + ((-60 if k < 0.6 * n_sessions else 20) if minute == 650 else 0)
            rows.append({"day": day, "mode": "live", "strategy": "ORB", "symbol": f"S{i}", "side": side,
                         "notional": 100_000.0, "net_pnl": bp / 1e4 * 100_000.0, "exit_reason": "t", "mkt_ret": mkt,
                         "daily_up": bool(rng.integers(0, 2)), "gap": rng.normal(0, 0.006),
                         "or_width": abs(rng.normal(0.01, 0.003)), "entry_minute": minute})
    return LR.label(pd.DataFrame(rows))


def _cell(r, pattern):
    return next((c for c in r["cells"] if c["pattern"] == pattern), None)


def test_too_few_sessions_says_how_many_and_judges_nothing():
    r = LM.mine(_trades(30))
    assert r["status"] == "INSUFFICIENT" and r["sessions"] == 30 and r["cells"] == []
    assert "30 of 40" in r["note"]


def test_a_pattern_that_repeats_out_of_sample_is_confirmed():
    r = LM.mine(_trades(60))
    c = _cell(r, "vs_market_today = against the market")
    assert c is not None and c["outcome"] == "CONFIRMED"
    assert c["discovery"]["t"] <= -LM.T_DISCOVER and c["confirmation"]["t"] <= -LM.T_CONFIRM
    assert r["confirmed"] >= 1


def test_a_pattern_found_by_luck_in_the_early_sessions_is_not_confirmed():
    r = LM.mine(_trades(60, real=False, late_only=True))
    c = _cell(r, "entry_time = 10:30-11:00")
    assert c is not None and c["outcome"] == "FAILED_CONFIRMATION", r["cells"]
    assert c["discovery"]["mean_bp"] < 0 and c["confirmation"]["mean_bp"] > 0


def test_pure_noise_flags_nothing():
    flagged = 0
    for seed in range(1, 6):
        df = _trades(60, seed, real=False)
        df["net_pnl"] = np.random.default_rng(seed).normal(0, 30, len(df)) / 1e4 * 100_000.0
        df = LR.label(df.drop(columns=["net_bp"]))
        flagged += LM.mine(df)["confirmed"]
    assert flagged == 0


def test_windows_are_split_in_time_and_do_not_overlap():
    d, c = LM.split_sessions([f"2026-10-{i:02d}" for i in range(1, 21)])
    assert d[-1] < c[0] and len(d) == 12 and len(c) == 8


def test_apply_moves_only_the_statuses_the_miner_owns_and_alerts_once():
    con = duckdb.connect(":memory:")
    con.execute(LR.SCHEMA)
    r = LM.mine(_trades(60))
    newly = LM.apply(con, r, today="2026-12-31")
    assert "vs_market_today = against the market" in newly
    status = dict(con.execute("SELECT pattern, status FROM hypotheses").fetchall())
    assert status["vs_market_today = against the market"] == "CONFIRMED"
    assert con.execute("SELECT status FROM loss_mining_runs").fetchone()[0] == "MINED"
    assert LM.apply(con, r, today="2027-01-01") == []                 # already CONFIRMED: no second alert
    # a status the owner set is never overwritten
    con.execute("UPDATE hypotheses SET status = 'SHADOW' WHERE pattern = 'vs_market_today = against the market'")
    LM.apply(con, r, today="2027-01-02")
    assert con.execute("SELECT status FROM hypotheses WHERE pattern = 'vs_market_today = against the market'").fetchone()[0] == "SHADOW"


def test_failed_confirmation_is_recorded_so_the_noise_pattern_stays_dead():
    con = duckdb.connect(":memory:")
    con.execute(LR.SCHEMA)
    LM.apply(con, LM.mine(_trades(60, real=False, late_only=True)), today="2026-12-31")
    st = dict(con.execute("SELECT pattern, status FROM hypotheses").fetchall())
    assert st["entry_time = 10:30-11:00"] == "FAILED_CONFIRMATION"


def test_an_insufficient_run_is_logged_and_changes_no_hypothesis():
    con = duckdb.connect(":memory:")
    con.execute(LR.SCHEMA)
    r = LM.mine(_trades(20))
    assert LM.apply(con, r, today="2026-11-12") == []
    assert con.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0] == 0
    assert con.execute("SELECT status, sessions FROM loss_mining_runs").fetchone() == ("INSUFFICIENT", 20)


def test_alert_text_says_nothing_was_changed():
    a = LM.alerts(["vs_market_today = against the market"], "2026-12-31")
    assert len(a) == 1 and "Nothing changed" in a[0]["body"] and a[0]["key"].startswith("loss-confirmed:")
