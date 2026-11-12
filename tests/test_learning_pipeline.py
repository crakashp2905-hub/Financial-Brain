"""One night of the loop, and a whole history of nights: stages are isolated, a night re-run changes nothing, and a real effect is found,
confirmed and announced exactly once."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from financial_brain.learning import calibrate as C
from financial_brain.learning import pipeline as PL
from financial_brain.learning.store import Store

CELL = "ORB|vs_market_today|against the market"


def _raw(led: pd.DataFrame) -> pd.DataFrame:
    """A labelled synthetic ledger as the ledger's own columns (what ``collect`` would read)."""
    rng = np.random.default_rng(0)
    d = led.copy()
    sign = np.where(d["side"] == "long", 1.0, -1.0)
    d["stop"] = d["entry_px"] * (1 - 0.01 * sign)
    d["target"] = d["entry_px"] * (1 + 0.02 * sign)
    d["entry_ts"] = d["day"] + "T09:35:00+05:30"
    d["exit_ts"] = d["day"] + "T15:15:00+05:30"
    d["exit_reason"] = rng.choice(["stop", "target", "time"], len(d), p=[.2, .1, .7])
    d["costs"] = 8.0
    d["gross_pnl"] = d["net_pnl"] + d["costs"]
    d["version"] = "live-v1"
    return d[["trade_id", "mode", "version", "day", "strategy", "symbol", "side", "qty", "entry_ts", "entry_px", "stop", "target",
              "exit_ts", "exit_reason", "gross_pnl", "costs", "net_pnl", "bucket", "entry_decision_px", "spread_bp", "mkt_ret",
              "daily_up", "gap", "or_width", "entry_minute", "entry_vwap"]].assign(shortfall_bp=2.0)


def _inputs(raw: pd.DataFrame, upto: str | None = None, *, extra_sessions: list[str] | None = None, lost: dict | None = None):
    days = sorted(raw["day"].unique())
    if upto:
        raw = raw[raw["day"] <= upto]
        days = [d for d in days if d <= upto]
    runs = pd.DataFrame([{"day": d, "mode": "live", "status": "complete", "symbols": 200, "polls": 800, "note": ""} for d in days])
    sessions = sorted(set(days) | set(extra_sessions or []))
    if lost:
        runs = pd.concat([runs, pd.DataFrame([{"day": d, "mode": "live", "status": "aborted", "symbols": 200, "polls": 0, "note": n}
                                              for d, n in lost.items()])], ignore_index=True)
    return PL.Inputs(trades=raw, runs=runs, regimes=pd.DataFrame(columns=["business_date", "regime", "vix"]), sessions=sessions)


def _planted(n=70, seed=1, bp=45.0):
    return _raw(C.synthetic_ledger(n, seed=seed, plant=("vs_market_today", "against the market", bp)))


def test_regimes_are_the_previous_sessions_not_the_same_days():
    r = pd.DataFrame({"business_date": ["2026-11-02", "2026-11-03", "2026-11-04"], "regime": ["RISK_ON", "RISK_OFF", "NEUTRAL"],
                      "vix": [14.0, 22.0, 17.0]})
    ctx = PL.day_context(r)
    assert ctx == {"2026-11-03": {"regime_prev": "RISK_ON", "vix": 14.0}, "2026-11-04": {"regime_prev": "RISK_OFF", "vix": 22.0}}


def test_a_night_with_no_trades_yet_runs_cleanly_and_says_it_is_collecting(tmp_path):
    with Store(":memory:") as st:
        rep = PL.process(st, _inputs(_raw(C.synthetic_ledger(5, seed=1))), today="2027-02-01", audit=[], task_registered=lambda n: True)
    assert rep["ok"] and rep["stages"]["protocol"]["status"] == "COLLECTING" and rep["stages"]["protocol"]["have"] == 5


def test_every_stage_runs_and_the_run_is_recorded():
    raw = _planted(30)
    with Store(":memory:") as st:
        rep = PL.process(st, _inputs(raw), today="2027-03-01", audit=[], task_registered=lambda n: True)
        assert rep["ok"] and set(rep["stages"]) == {"label", "autopsy", "incidents", "protocol", "shadow"}
        assert len(st.autopsies()) == len(raw) and st.runs().iloc[-1]["status"] == "ok"
        assert rep["stages"]["protocol"]["status"] == "RAN"


def test_one_failing_stage_does_not_stop_the_others_and_is_recorded(monkeypatch):
    from financial_brain.learning import autopsy as AU

    def boom(*a, **k):
        raise RuntimeError("archive exploded")

    monkeypatch.setattr(AU, "build", boom)
    with Store(":memory:") as st:
        rep = PL.process(st, _inputs(_planted(30)), today="2027-03-01", audit=[], task_registered=lambda n: True)
        assert not rep["ok"] and not rep["stages"]["autopsy"]["ok"] and "archive exploded" in rep["stages"]["autopsy"]["error"]
        assert rep["stages"]["protocol"]["ok"] and rep["stages"]["incidents"]["ok"]
        run = st.runs().iloc[-1]
        assert run["status"] == "partial" and "autopsy" in run["note"]


def test_rerunning_a_night_changes_nothing():
    raw = _planted(40)
    with Store(":memory:") as st:
        PL.process(st, _inputs(raw), today="2027-03-01", audit=[], task_registered=lambda n: True)
        n = len(st.events())
        PL.process(st, _inputs(raw), today="2027-03-01", audit=[], task_registered=lambda n: True)
        assert len(st.events()) == n and len(st.runs()) == 1


def test_a_real_effect_is_found_confirmed_and_announced_exactly_once_over_a_whole_history():
    raw = _planted(60, seed=1, bp=45.0)
    days = sorted(raw["day"].unique())
    alerts = []
    with Store(":memory:") as st:
        for d in days[11:]:
            rep = PL.process(st, _inputs(raw, d), today=d, audit=[], task_registered=lambda n: True)
            alerts += rep["alerts"]
        lessons = st.lessons()
        confirmed = [x for x in lessons.values() if x["stage"] == "CONFIRMED"]
        assert CELL in {x["cell"] for x in confirmed}
        keys = [a["key"] for a in alerts if a["key"].startswith("learn-confirmed:")]
        assert len(keys) == len(set(keys)) == len(confirmed)                 # one alert per confirmed lesson, never repeated
        text = next(a for a in alerts if a["key"].startswith("learn-confirmed:"))["body"]
        assert "Nothing changed" in text and "fb learn draft" in text


def test_a_lost_session_raises_one_alert_naming_the_cause_and_the_guard_state():
    raw = _planted(20)
    days = sorted(raw["day"].unique())
    lost_day = "2027-02-01"
    inp = _inputs(raw, extra_sessions=[lost_day], lost={lost_day: "quotes failing: no Kite access token for today"})
    with Store(":memory:") as st:
        rep = PL.process(st, inp, today=lost_day, audit=[], task_registered=lambda n: False)
        a = [x for x in rep["alerts"] if x["key"].startswith("learn-incident:")]
        assert len(a) == 1 and "NO_LOGIN" in a[0]["title"] and "GUARD NOT ACTIVE" in a[0]["body"]
        assert rep["ops"]["availability"]["lost"] == 1 and days
        assert st.incidents()["cause"].tolist() == ["NO_LOGIN"]


def test_a_rules_file_that_is_invalid_fails_only_the_shadow_stage(tmp_path):
    bad = tmp_path / "rules.json"
    bad.write_text('{"schema": "nope", "rules": []}', encoding="utf-8")
    with Store(":memory:") as st:
        rep = PL.process(st, _inputs(_planted(20)), today="2027-03-01", rules_path=bad, audit=[], task_registered=lambda n: True)
    assert not rep["stages"]["shadow"]["ok"] and rep["stages"]["protocol"]["ok"]


def test_the_digest_goes_out_on_fridays_once_a_week():
    assert PL.weekly("2026-11-13") and not PL.weekly("2026-11-12")
    assert PL.iso_week("2026-11-13") == "2026-W46"


def test_collect_refuses_politely_while_the_live_run_holds_the_ledger(tmp_path):
    import duckdb
    p = tmp_path / "paper.duckdb"
    holder = duckdb.connect(str(p))
    holder.execute("CREATE TABLE trades (x INTEGER)")
    try:
        with pytest.raises(PL.LedgerBusy, match="after the close"):
            PL.collect(ledger=p, archive=tmp_path / "none.duckdb", research_db=None, reports=tmp_path)
    finally:
        holder.close()
