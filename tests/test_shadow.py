"""The live-v2 shadow: skip rules enter only when registered, are judged only on later sessions, and never touch v1."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from financial_brain.paper import intraday_runner as IR
from financial_brain.paper import loss_review as LR
from financial_brain.paper import shadow as SH

REG = [{"prereg": "PRE-REGISTERED skip rule against the market.md", "status": "OK"}]


def _file(tmp_path, rules):
    p = tmp_path / "rules.json"
    p.write_text(json.dumps({"schema": SH.SCHEMA_ID, "rules": rules}), encoding="utf-8")
    return p


def _rule(**kw):
    base = {"id": "SK-0001", "dimension": "vs_market_today", "value": "against the market", "strategy": "ORB",
            "prereg": "PRE-REGISTERED skip rule against the market", "active_from": "2027-01-04"}
    return {**base, **kw}


# ------------------------------------------------------------------------------------------------ the rule file

def test_no_rules_file_or_an_empty_one_means_no_shadow(tmp_path):
    assert SH.load_rules(tmp_path / "missing.json") == []
    assert SH.load_rules(_file(tmp_path, []), audit=[]) == []
    assert SH.load_rules(SH.RULES_FILE) == []                      # the committed file is empty on purpose


def test_a_registered_rule_loads(tmp_path):
    rules = SH.load_rules(_file(tmp_path, [_rule()]), audit=REG)
    assert [r.id for r in rules] == ["SK-0001"] and rules[0].pattern == "vs_market_today = against the market"


@pytest.mark.parametrize("status", ["RESULT_BEFORE_REGISTRATION", "CHANGED_AFTER_RESULT", None])
def test_a_rule_without_a_valid_pre_registration_is_refused(tmp_path, status):
    audit = [{"prereg": "PRE-REGISTERED skip rule against the market.md", "status": status}] if status else []
    with pytest.raises(SH.ShadowError, match="written down|registered"):
        SH.load_rules(_file(tmp_path, [_rule()]), audit=audit)


def test_malformed_rules_are_refused(tmp_path):
    for bad, msg in ((_rule(dimension="colour"), "unknown dimension"), (_rule(active_from="soon"), "ISO date"),
                     ({k: v for k, v in _rule().items() if k != "prereg"}, "missing")):
        with pytest.raises(SH.ShadowError, match=msg):
            SH.load_rules(_file(tmp_path, [bad]), audit=REG)
    with pytest.raises(SH.ShadowError, match="duplicate"):
        SH.load_rules(_file(tmp_path, [_rule(), _rule()]), audit=REG)
    p = tmp_path / "wrong.json"
    p.write_text(json.dumps({"schema": "other", "rules": [_rule()]}), encoding="utf-8")
    with pytest.raises(SH.ShadowError, match="schema"):
        SH.load_rules(p, audit=REG)


# ------------------------------------------------------------------------------------------------ evaluation

def _live(n_days: int, *, against_bp: float, with_bp: float = 5.0, start="2027-01-04", seed=3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(start, periods=n_days).strftime("%Y-%m-%d")
    rows = []
    for day in days:
        mkt = rng.choice([-0.004, 0.004])
        for i in range(10):
            side = "long" if i % 2 == 0 else "short"
            against = (mkt > 0) != (side == "long")
            bp = rng.normal(0, 25) + (against_bp if against else with_bp)
            rows.append({"trade_id": f"{day}-{i}", "mode": "live", "day": day, "strategy": "ORB", "symbol": f"S{i}",
                         "side": side, "notional": 100_000.0, "net_pnl": bp / 1e4 * 100_000.0, "exit_reason": "t",
                         "mkt_ret": mkt, "daily_up": True, "gap": 0.0, "or_width": 0.01, "entry_minute": 600,
                         "entry_ts": f"{day}T10:00:00", "exit_ts": f"{day}T15:15:00"})
    return LR.label(pd.DataFrame(rows))


RULE = SH.SkipRule(id="SK-0001", dimension="vs_market_today", value="against the market", strategy="ORB",
                   prereg="PRE-REGISTERED skip rule against the market", active_from="2027-01-04")


def test_before_activation_nothing_is_judged():
    r = SH.evaluate_rule(RULE, _live(70, against_bp=-40), today="2026-12-01")
    assert r["status"] == "SCHEDULED" and r["skipped_trades"] == 0


def test_too_few_sessions_is_still_shadow_even_if_the_rule_looks_great():
    r = SH.evaluate_rule(RULE, _live(25, against_bp=-60), today="2027-03-01")
    assert r["status"] == "SHADOW" and r["skipped_trades"] > 0 and r["skipped_bp"] < 0


def test_a_rule_that_keeps_removing_losers_passes_and_v2_beats_v1():
    r = SH.evaluate_rule(RULE, _live(70, against_bp=-40), today="2027-05-01")
    assert r["status"] == "PASSING" and r["skipped_t"] <= SH.T_PASS
    assert r["v2_net_inr"] > r["v1_net_inr"]
    assert r["v2_net_inr"] == pytest.approx(r["v1_net_inr"] - r["skipped_net_inr"])


def test_a_rule_that_would_have_removed_winners_fails():
    r = SH.evaluate_rule(RULE, _live(70, against_bp=+25), today="2027-05-01")
    assert r["status"] == "FAILING" and r["skipped_bp"] > 0 and r["v2_net_inr"] < r["v1_net_inr"]


def test_only_sessions_from_activation_are_judged():
    early = _live(40, against_bp=-80, start="2026-11-02")           # a losing stretch before the rule existed
    late = _live(70, against_bp=+25, start="2027-01-04", seed=4)    # then the pattern stopped
    both = pd.concat([early, late], ignore_index=True)
    r = SH.evaluate_rule(RULE, both, today="2027-05-01")
    assert r["days"] == 70 and r["status"] == "FAILING"             # the early losers are not borrowed


def test_the_strategy_filter_is_honoured():
    df = _live(70, against_bp=-40)
    df["strategy"] = "GAP"
    r = SH.evaluate_rule(RULE, df, today="2027-05-01")
    assert r["skipped_trades"] == 0


def test_exposure_cap_proximity_is_counted():
    df = _live(3, against_bp=0)                                     # ten Rs 1 lakh trades open together each day
    assert SH.peak_exposure_days(df, cap_inr=1_050_000) == 3
    assert SH.peak_exposure_days(df, cap_inr=5_000_000) == 0


def test_persist_stores_results_per_trade_decisions_and_reports_status_changes(tmp_path):
    con = IR.ledger(tmp_path / "paper.duckdb")
    live = _live(70, against_bp=-40)
    cols = ["trade_id", "mode", "version", "day", "strategy", "symbol", "side", "qty", "entry_ts", "entry_px", "exit_ts",
            "exit_px", "net_pnl", "mkt_ret", "daily_up", "gap", "or_width", "entry_minute"]
    for r in live.itertuples():
        con.execute(f"INSERT INTO trades ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    [r.trade_id, "live", "live-v1", r.day, "ORB", r.symbol, r.side, 100, r.entry_ts, 1000.0, r.exit_ts,
                     1000.0, r.net_pnl, r.mkt_ret, True, 0.0, 0.01, 600])
    results = SH.evaluate(con, [RULE], today="2027-05-01")
    assert results[0]["status"] == "PASSING"
    assert SH.persist(con, [RULE], results, today="2027-05-01") == ["SK-0001"]
    assert SH.persist(con, [RULE], results, today="2027-05-02") == []     # unchanged: no second report
    n_skip = con.execute("SELECT COUNT(*) FROM shadow_decisions WHERE would_skip").fetchone()[0]
    assert n_skip == results[0]["skipped_trades"] > 0
    assert con.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == len(live)   # v1's ledger is untouched
    found = SH.alerts(results, ["SK-0001"], "2027-05-01")
    assert len(found) == 1 and "Nothing was changed" in found[0]["body"]


def test_render_without_rules_explains_how_one_arrives():
    assert "pre-registers" in SH.render([])
