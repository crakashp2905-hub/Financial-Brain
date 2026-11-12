"""The surfaces: what needs the owner, the status view, the terminal panel and the weekly digest; the A/B of v1 against v2; the CLI."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from financial_brain.learning import activation as AC
from financial_brain.learning import autopsy as AU
from financial_brain.learning import governance as GV
from financial_brain.learning import report as RP
from financial_brain.learning import shadow as SH
from financial_brain.learning.store import Store
from financial_brain.paper import intraday_runner as IR

CELL = "ORB|vs_market_today|against the market"
NOM = {"cell": CELL, "strategy": "ORB", "dimension": "vs_market_today", "value": "against the market", "family": "market",
       "family_size": 9, "d0": "2026-12-04", "days": 24, "trades": 160, "mean_bp": -31.0, "contrast_bp": -42.0, "contrast_sd": 30.0,
       "p": 0.0004, "q": 0.003, "batch_size": 1}
DEC = {"kind": "CONFIRMED", "trades": 140, "sessions_after": 22, "mean_bp": -28.0, "contrast_bp": -36.0, "p": 0.002, "alpha": 0.05}


def _store_with_confirmed():
    st = Store(":memory:")
    st.append("L-abc12345", "NOMINATED", session="2026-12-04", payload=NOM)
    st.append("L-abc12345", "CONFIRMED", session="2027-01-06", payload=DEC)
    return st


def _auto():
    rows = []
    for i, (cause, pnl) in enumerate([("NOISE_STOP", -500.0), ("NOISE_STOP", -300.0), ("COST_DOMINATED", -50.0), ("WIN", 900.0)]):
        rows.append({"trade_id": f"t{i}", "mode": "live", "day": f"2026-11-0{2 + i % 3}", "symbol": "A", "strategy": "ORB", "side": "long",
                     "net_pnl": pnl, "gross_pnl": pnl + 10, "net_bp": pnl / 100, "r_gross": 0.0, "mae_r": 0.5, "mfe_r": 0.5,
                     "minutes_in": 30, "exit_reason": "stop", "reversed_after_exit": True, "has_path": True, "classes": cause,
                     "primary_class": cause})
    return pd.DataFrame(rows)


def test_a_confirmed_lesson_with_no_rule_is_the_first_thing_that_needs_the_owner():
    with _store_with_confirmed() as st:
        ask = RP.needs_you(st.lessons(), None, None)
    assert len(ask) == 1 and "L-abc12345" in ask[0] and "fb learn draft L-abc12345" in ask[0]


def test_once_registered_it_stops_asking_and_a_passing_rule_asks_for_a_decision():
    with _store_with_confirmed() as st:
        st.append("L-abc12345", "REGISTERED", session="SK-0001", payload={"rule": "SK-0001", "cell": CELL})
        shadow = [{"rule_id": "SK-0001", "cell": CELL, "status": "PASSING", "v1_net_inr": 100.0, "v2_net_inr": 900.0, "days": 70}]
        ask = RP.needs_you(st.lessons(), shadow, None)
    assert len(ask) == 1 and "fb learn approve SK-0001" in ask[0] and "800" in ask[0]


def test_an_unscheduled_guard_and_an_unguarded_cause_are_named_with_what_to_do():
    ops = {"availability": {"sessions": 8, "completed": 3, "lost": 5, "rate": .375},
           "causes": [{"cause": "NO_LOGIN", "sessions_lost": 5, "state": "GUARD NOT ACTIVE", "guard": "pre-open check", "task": "FinanceBrain Preflight"},
                      {"cause": "NO_RUN", "sessions_lost": 1, "state": "UNGUARDED", "guard": None}]}
    with Store(":memory:") as st:
        ask = RP.needs_you(st.lessons(), None, ops)
    assert any("register the 'FinanceBrain Preflight' task" in a for a in ask) and any("nothing guards against it" in a for a in ask)


def test_the_status_view_says_how_far_the_evidence_is_and_what_is_confirmed():
    with _store_with_confirmed() as st:
        st.record_run(run_on="2027-01-06", session="2027-01-06", sessions=14, trades=800, status="ok")
        st.replace_autopsies(_auto())
        text = RP.status_text(st, today="2027-01-07", shadow=[], ops=None, depth={"ok_sessions": 30, "register_at": 120, "stage": "collecting"})
    for needle in ("Needs you", "14 live sessions", "800 closed trades", "CONFIRMED", CELL, "NOISE_STOP", "30 complete depth/OI sessions of 120"):
        assert needle in text, needle
    assert "screening starts at 12 sessions" not in text


def test_an_empty_ledger_gives_an_honest_status():
    with Store(":memory:") as st:
        text = RP.status_text(st, today="2027-01-07")
    assert "has not run yet" in text and "Needs you: nothing." in text and "lessons: none yet" in text


def test_the_terminal_panel_escapes_everything_and_degrades_to_a_sentence(tmp_path):
    assert "has not run yet" in RP.terminal_html(store_path=tmp_path / "none.duckdb")
    p = tmp_path / "l.duckdb"
    with Store(p) as st:
        st.append("L-x", "NOMINATED", session="s", payload={**NOM, "cell": "ORB|weekday|<script>alert(1)</script>"})
        st.append("L-x", "CONFIRMED", session="t", payload=DEC)
        st.record_run(run_on="2027-01-06", session="2027-01-06", sessions=30, trades=500, status="ok")
        st.replace_autopsies(_auto())
    html = RP.terminal_html(store_path=p, depth_path=tmp_path / "none.duckdb", today="2027-01-07")
    assert "&lt;script&gt;" in html and "<script>" not in html and "How trades lose" in html and "NOISE_STOP" in html


def test_the_digest_is_short_keyed_by_week_and_names_the_biggest_leak():
    with _store_with_confirmed() as st:
        st.replace_autopsies(_auto())
        d = RP.digest(st, week="2027-W01", today="2027-01-08", shadow=[], ops={"availability": {"sessions": 5, "completed": 4, "lost": 1, "rate": .8},
                                                                                "causes": []})
    assert d["key"] == "learn-digest:2027-W01" and len(d["body"]) <= 900
    assert "Biggest leak: NOISE_STOP" in d["body"] and "1 confirmed" in d["body"] and "need you" in d["body"] and "4/5" in d["body"]


# ------------------------------------------------------------------------------------------------ A/B

def _ledger(path, rows):
    con = IR.ledger(path)
    for i, (day, strat, sym, pnl) in enumerate(rows):
        con.execute("""INSERT INTO trades (trade_id, mode, version, day, strategy, symbol, side, qty, entry_ts, entry_px, exit_ts, exit_px,
                       net_pnl) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [f"{i}", "live", "v", day, strat, sym, "long", 10, f"{day}T10:00:00", 100.0, f"{day}T15:00:00", 101.0, pnl])
    con.close()


def test_ab_reports_the_declined_entries_the_extras_and_the_paired_difference(tmp_path):
    days = [f"2027-02-{d:02d}" for d in range(1, 9)]
    v1 = [(d, "ORB", s, p) for d in days for s, p in (("A", 100.0), ("B", -300.0), ("C", 50.0))]
    v2 = [(d, "ORB", s, p) for d in days for s, p in (("A", 100.0), ("C", 50.0))] + [("2027-02-03", "ORB", "D", -20.0)]
    _ledger(tmp_path / "paper_live.duckdb", v1)
    _ledger(tmp_path / "paper_live-live-v2-abc123.duckdb", v2)
    r = AC.ab(tmp_path / "paper_live.duckdb", AC.v2_ledgers(tmp_path))
    assert r["days"] == 8 and r["declined"] == 8 and r["declined_net_inr"] == pytest.approx(-2400.0)
    assert r["only_v2"] == 1 and r["only_v2_net_inr"] == pytest.approx(-20.0)
    assert r["diff_inr"] == pytest.approx(2400.0 - 20.0) and r["paired_t"] > 3
    assert "declined 8 entries" in AC.render_ab(r)


def test_ab_with_no_v2_ledger_or_no_common_days_says_so(tmp_path):
    _ledger(tmp_path / "paper_live.duckdb", [("2027-02-01", "ORB", "A", 1.0)])
    assert "no common sessions" in AC.render_ab(AC.ab(tmp_path / "paper_live.duckdb", []))
    _ledger(tmp_path / "paper_live-live-v2-x.duckdb", [("2027-03-01", "ORB", "A", 1.0)])
    assert "no common sessions" in AC.render_ab(AC.ab(tmp_path / "paper_live.duckdb", AC.v2_ledgers(tmp_path)))


def test_active_rules_come_only_from_current_human_approvals(tmp_path):
    rule = {"id": "SK-0001", "lesson": "L-a", "strategy": "ORB", "dimension": "direction", "value": "long", "prereg": "PRE-REGISTERED x",
            "active_from": "2027-01-04", "status": "ACTIVE"}
    now = datetime(2027, 3, 1, tzinfo=timezone.utc)
    shadow_rule = SH.SkipRule(id="SK-0001", lesson="L-a", strategy="ORB", dimension="direction", value="long", prereg="PRE-REGISTERED x",
                              active_from="2027-01-04")
    rule["approval"] = {"kind": GV.KIND, "subject": "SK-0001", "scope_sha256": GV.scope_sha(shadow_rule), "actor_id": "CR Ramesh",
                        "actor_type": "human", "approved_at_utc": now.isoformat(), "expires_at_utc": (now + timedelta(days=30)).isoformat(),
                        "adr_ref": "ADR-0004"}
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"schema": SH.SCHEMA_ID, "rules": [rule]}), encoding="utf-8")
    audit = [{"prereg": "PRE-REGISTERED x.md", "status": "OK"}]
    assert [r.id for r in AC.active_rules(p, audit=audit, now=now + timedelta(days=1))] == ["SK-0001"]
    assert AC.active_rules(p, audit=audit, now=now + timedelta(days=40)) == []            # expired: back to shadow
    kw = AC.cohort_args(AC.active_rules(p, audit=audit, now=now + timedelta(days=1)))
    assert kw["cohort"] == "live-v2" and set(kw["rules"]) == {"ORB"} and kw["day_context_fn"] is None


def test_day_context_reads_the_previous_sessions_regime_and_vix(tmp_path):
    import duckdb
    p = tmp_path / "r.duckdb"
    con = duckdb.connect(str(p))
    con.execute("CREATE TABLE market_regime (business_date DATE, version VARCHAR, regime VARCHAR, vix DOUBLE)")
    con.execute("INSERT INTO market_regime VALUES ('2026-11-09','v2','NEUTRAL',17.4), ('2026-11-11','v2','RISK_ON',16.0), "
                "('2026-11-12','v1','CRISIS',99.0)")
    con.close()
    assert AC.day_context("2026-11-12", p) == {"regime_prev": "RISK_ON", "vix": 16.0}
    assert AC.day_context("2026-11-09", p) == {}


# ------------------------------------------------------------------------------------------------ the command line

def test_the_learn_commands_exist_and_refuse_safely(tmp_path, capsys):
    from financial_brain.cli import main
    store = str(tmp_path / "l.duckdb")
    assert main(["learn", "status", "--store", store]) == 0
    assert "has not run yet" in capsys.readouterr().out
    assert main(["learn", "approve", "SK-9999", "--actor", "CR Ramesh", "--store", store]) == 2      # no terminal in a test run
    assert "refused" in capsys.readouterr().out
    assert main(["learn", "draft", "L-nope", "--store", store]) == 2
    assert main(["paper-intraday", "--v2"]) == 3                                                    # nothing ACTIVE: starts nothing
    assert "nothing was started" in capsys.readouterr().out


def test_autopsy_render_uses_the_stored_rows():
    with Store(":memory:") as st:
        st.replace_autopsies(_auto())
        assert "NOISE_STOP" in AU.render(st.autopsies())
