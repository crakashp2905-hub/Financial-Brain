"""Shadow evaluation and human-only approval: a rule needs a prior registration, a person, a PASSING verdict, and keeps no
approval it was not given for exactly what it says."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from financial_brain.learning import calibrate as C
from financial_brain.learning import features as F
from financial_brain.learning import governance as G
from financial_brain.learning import shadow as SH
from financial_brain.learning.store import Store

AUDIT_OK = [{"prereg": "PRE-REGISTERED skip rule SK-0001.md", "status": "OK"}]
NOW = datetime(2027, 3, 1, 12, 0, tzinfo=timezone.utc)


def _rule_json(**kw):
    base = {"id": "SK-0001", "lesson": "L-abc12345", "strategy": "ORB", "dimension": "vs_market_today",
            "value": "against the market", "prereg": "PRE-REGISTERED skip rule SK-0001", "active_from": "2027-01-04",
            "status": "SHADOW"}
    return {**base, **kw}


def _file(tmp_path, *rules):
    p = tmp_path / "rules.json"
    p.write_text(json.dumps({"schema": SH.SCHEMA_ID, "rules": list(rules)}), encoding="utf-8")
    return p


def _ledger(n, bp_against, *, seed=3, start="2027-01-04"):
    led = C.synthetic_ledger(n, seed=seed, start=start)
    led = led[led["strategy"] == "ORB"].copy()
    m = led["vs_market_today"] == "against the market"
    led.loc[m, "net_pnl"] = led.loc[m, "notional"] * bp_against / 1e4 + np.random.default_rng(seed).normal(0, 25, m.sum()) / 1e4 * led.loc[m, "notional"]
    led["net_bp"] = led["net_pnl"] / led["notional"] * 1e4
    led["entry_ts"] = led["day"] + "T10:00:00"
    led["exit_ts"] = led["day"] + "T15:15:00"
    return led


RULE = SH.SkipRule(id="SK-0001", lesson="L-abc12345", strategy="ORB", dimension="vs_market_today", value="against the market",
                   prereg="PRE-REGISTERED skip rule SK-0001", active_from="2027-01-04")


# ------------------------------------------------------------------------------------------------ the rule file

def test_the_committed_rules_file_is_empty_on_purpose():
    assert SH.load_rules(SH.RULES_FILE) == []


def test_a_registered_rule_loads_as_shadow(tmp_path):
    (r,) = SH.load_rules(_file(tmp_path, _rule_json()), audit=AUDIT_OK, now=NOW)
    assert r.effective_status == "SHADOW" and r.cell == "ORB|vs_market_today|against the market" and r.lesson_id == "L-abc12345"


@pytest.mark.parametrize("status", ["RESULT_BEFORE_REGISTRATION", "CHANGED_AFTER_RESULT", None])
def test_a_rule_without_a_valid_pre_registration_is_refused(tmp_path, status):
    audit = [{"prereg": "PRE-REGISTERED skip rule SK-0001.md", "status": status}] if status else []
    with pytest.raises(SH.ShadowError, match="written down|registered"):
        SH.load_rules(_file(tmp_path, _rule_json()), audit=audit, now=NOW)


@pytest.mark.parametrize("bad,msg", [({"dimension": "colour"}, "unknown dimension"), ({"value": "sideways"}, "not a value"),
                                      ({"strategy": "VWAP"}, "strategy must"), ({"active_from": "soon"}, "ISO date"),
                                      ({"status": "LIVE"}, "status must")])
def test_malformed_rules_are_refused(tmp_path, bad, msg):
    with pytest.raises(SH.ShadowError, match=msg):
        SH.load_rules(_file(tmp_path, _rule_json(**bad)), audit=AUDIT_OK, now=NOW)
    with pytest.raises(SH.ShadowError, match="duplicate"):
        SH.load_rules(_file(tmp_path, _rule_json(), _rule_json()), audit=AUDIT_OK, now=NOW)


# ------------------------------------------------------------------------------------------------ evaluation

def test_before_activation_and_with_too_few_sessions_nothing_is_judged():
    assert SH.evaluate_rule(RULE, _ledger(80, -60), today="2026-12-01")["status"] == "SCHEDULED"
    r = SH.evaluate_rule(RULE, _ledger(25, -60), today="2027-03-01")
    assert r["status"] == "COLLECTING" and r["skipped_bp"] < 0


def test_a_rule_that_keeps_removing_losers_passes_and_v2_beats_v1():
    r = SH.evaluate_rule(RULE, _ledger(80, -60), today="2027-06-01")
    assert r["status"] == "PASSING" and r["skipped_t"] <= SH.T_PASS and r["v2_net_inr"] > r["v1_net_inr"]
    assert r["v2_net_inr"] == pytest.approx(r["v1_net_inr"] - r["skipped_net_inr"])


def test_a_rule_that_would_have_removed_winners_fails():
    r = SH.evaluate_rule(RULE, _ledger(80, +40), today="2027-06-01")
    assert r["status"] == "FAILING" and r["v2_net_inr"] < r["v1_net_inr"]


def test_only_sessions_from_activation_are_judged():
    early = _ledger(40, -90, start="2026-11-02", seed=4)
    late = _ledger(80, +40, start="2027-01-04", seed=5)
    r = SH.evaluate_rule(RULE, pd.concat([early, late], ignore_index=True), today="2027-06-01")
    assert r["days"] == 80 and r["status"] == "FAILING"


def test_a_retired_rule_is_not_judged():
    r = SH.SkipRule(**{**RULE.__dict__, "status": "RETIRED"})
    assert SH.evaluate_rule(r, _ledger(80, -60), today="2027-06-01")["status"] == "RETIRED"


def test_the_ledger_records_registration_and_each_verdict_and_reports_changes_once():
    with Store(":memory:") as st:
        res = SH.evaluate([RULE], [RULE], today="2027-06-01") if False else [SH.evaluate_rule(RULE, _ledger(80, -60), today="2027-06-01")]
        assert SH.record(st, [RULE], res, "2027-06-01") == ["SK-0001"]
        assert SH.record(st, [RULE], res, "2027-06-02") == []                 # same verdict: no second report
        kinds = [e["kind"] for e in st.events("L-abc12345")]
        assert kinds[:2] == ["REGISTERED", "SHADOW"] and kinds.count("SHADOW_RESULT") == 2
        assert st.lessons()["L-abc12345"]["shadow"]["status"] == "PASSING"
        (a,) = SH.alerts(res, ["SK-0001"])
        assert "fb learn approve SK-0001" in a["body"] and "Nothing was changed" in a["body"]


# ------------------------------------------------------------------------------------------------ approval

def _passing_store(tmp_path, status="PASSING"):
    st = Store(":memory:")
    st.append("L-abc12345", "REGISTERED", session="SK-0001", payload={"rule": "SK-0001"})
    st.append("L-abc12345", "SHADOW_RESULT", session="2027-06-01:SK-0001", payload={
        "rule": "SK-0001", "status": status, "skipped_trades": 90, "days": 70, "v1_net_inr": 100.0, "v2_net_inr": 400.0,
        "skipped_net_inr": -300.0, "pattern": "x", "strategy": "ORB"})
    return st


def test_approval_needs_a_person_at_a_terminal(tmp_path):
    p = _file(tmp_path, _rule_json())
    with _passing_store(tmp_path) as st, pytest.raises(G.GovernanceError, match="person at a terminal"):
        G.approve("SK-0001", "CR Ramesh", st, path=p, interactive=False, input_fn=lambda _: "APPROVE SK-0001", now=NOW)
    assert json.loads(p.read_text())["rules"][0]["status"] == "SHADOW"


@pytest.mark.parametrize("name", ["claude", "agent:research-bot", "Claude Code", "cron", "", "gpt-5"])
def test_software_names_cannot_approve(tmp_path, name):
    with _passing_store(tmp_path) as st, pytest.raises(G.GovernanceError, match="not the name of a person"):
        G.approve("SK-0001", name, st, path=_file(tmp_path, _rule_json()), interactive=True, input_fn=lambda _: "APPROVE SK-0001",
                  now=NOW)


@pytest.mark.parametrize("status", ["COLLECTING", "INCONCLUSIVE", "FAILING"])
def test_only_a_passing_rule_can_be_approved(tmp_path, status):
    with _passing_store(tmp_path, status) as st, pytest.raises(G.GovernanceError, match="not PASSING"):
        G.approve("SK-0001", "CR Ramesh", st, path=_file(tmp_path, _rule_json()), interactive=True,
                  input_fn=lambda _: "APPROVE SK-0001", now=NOW)


def test_a_wrong_confirmation_changes_nothing(tmp_path):
    p = _file(tmp_path, _rule_json())
    with _passing_store(tmp_path) as st, pytest.raises(G.GovernanceError, match="nothing changed"):
        G.approve("SK-0001", "CR Ramesh", st, path=p, interactive=True, input_fn=lambda _: "yes", now=NOW)
    assert json.loads(p.read_text())["rules"][0]["status"] == "SHADOW"


def test_a_confirmed_approval_activates_the_rule_and_is_recorded_with_the_approvers_name(tmp_path):
    p = _file(tmp_path, _rule_json())
    with _passing_store(tmp_path) as st:
        ap = G.approve("SK-0001", "CR Ramesh", st, path=p, interactive=True, input_fn=lambda _: "APPROVE SK-0001", now=NOW,
                       out=lambda *_: None)
        assert ap["actor_type"] == "human" and ap["adr_ref"] == "ADR-0004"
        assert [e["kind"] for e in st.events("L-abc12345")][-2:] == ["APPROVED", "ACTIVATED"]
        assert st.lessons()["L-abc12345"]["stage"] == "ACTIVE" and st.events("L-abc12345")[-1]["actor"] == "CR Ramesh"
    (r,) = SH.load_rules(p, audit=AUDIT_OK, now=NOW + timedelta(days=10))
    assert r.effective_status == "ACTIVE" and r.problem is None


def test_an_approval_expires_back_to_shadow(tmp_path):
    p = _file(tmp_path, _rule_json())
    with _passing_store(tmp_path) as st:
        G.approve("SK-0001", "CR Ramesh", st, path=p, interactive=True, input_fn=lambda _: "APPROVE SK-0001", now=NOW, days=30,
                  out=lambda *_: None)
    (r,) = SH.load_rules(p, audit=AUDIT_OK, now=NOW + timedelta(days=31))
    assert r.effective_status == "SHADOW" and "expired" in r.problem


def test_editing_an_approved_rule_voids_the_approval(tmp_path):
    p = _file(tmp_path, _rule_json())
    with _passing_store(tmp_path) as st:
        G.approve("SK-0001", "CR Ramesh", st, path=p, interactive=True, input_fn=lambda _: "APPROVE SK-0001", now=NOW,
                  out=lambda *_: None)
    doc = json.loads(p.read_text())
    doc["rules"][0]["value"] = "with the market"                          # the rule is changed after it was approved
    p.write_text(json.dumps(doc), encoding="utf-8")
    (r,) = SH.load_rules(p, audit=AUDIT_OK, now=NOW)
    assert r.effective_status == "SHADOW" and "stale approval" in r.problem


def test_an_active_flag_without_an_approval_does_not_take_effect(tmp_path):
    (r,) = SH.load_rules(_file(tmp_path, _rule_json(status="ACTIVE")), audit=AUDIT_OK, now=NOW)
    assert r.effective_status == "SHADOW" and "no approval" in r.problem


def test_a_forged_approval_with_a_software_actor_is_not_honoured(tmp_path):
    ap = {"kind": G.KIND, "subject": "SK-0001", "scope_sha256": G.scope_sha(RULE), "actor_id": "claude", "actor_type": "human",
          "approved_at_utc": NOW.isoformat(), "expires_at_utc": (NOW + timedelta(days=30)).isoformat(), "adr_ref": "ADR-0004"}
    (r,) = SH.load_rules(_file(tmp_path, _rule_json(status="ACTIVE", approval=ap)), audit=AUDIT_OK, now=NOW)
    assert r.effective_status == "SHADOW" and "cannot approve" in r.problem


def test_retiring_needs_a_person_a_reason_and_the_typed_word(tmp_path):
    p = _file(tmp_path, _rule_json(status="ACTIVE"))
    with _passing_store(tmp_path) as st:
        with pytest.raises(G.GovernanceError):
            G.retire("SK-0001", "CR Ramesh", "", st, path=p, interactive=True, input_fn=lambda _: "RETIRE SK-0001")
        with pytest.raises(G.GovernanceError, match="person"):
            G.retire("SK-0001", "CR Ramesh", "decayed", st, path=p, interactive=False, input_fn=lambda _: "RETIRE SK-0001")
        G.retire("SK-0001", "CR Ramesh", "stopped holding", st, path=p, interactive=True, input_fn=lambda _: "RETIRE SK-0001")
        assert st.lessons()["L-abc12345"]["stage"] == "RETIRED"
    assert json.loads(p.read_text())["rules"][0]["status"] == "RETIRED"


# ------------------------------------------------------------------------------------------------ register and draft

def _confirmed_lesson():
    nom = {"cell": "ORB|vs_market_today|against the market", "strategy": "ORB", "dimension": "vs_market_today",
           "value": "against the market", "family": "market", "family_size": 9, "d0": "2026-12-04", "days": 24, "trades": 160,
           "mean_bp": -31.0, "contrast_bp": -42.0, "contrast_sd": 30.0, "p": 0.0004, "q": 0.003, "batch_size": 1}
    dec = {"kind": "CONFIRMED", "trades": 140, "sessions_after": 22, "mean_bp": -28.0, "contrast_bp": -36.0, "p": 0.002,
           "alpha": 0.05, "session": "2027-01-06"}
    return {"id": "L-abc12345", "stage": "CONFIRMED", "nomination": nom, "decision": dec, "cell": nom["cell"]}


def test_the_draft_preregistration_states_the_evidence_the_rule_and_the_ways_it_can_fail():
    text = G.draft(_confirmed_lesson(), "SK-0001")
    for needle in ("PRE-REGISTERED skip rule SK-0001", "vs_market_today", "-42.0 bp worse", "PASSING", "FAILING", "expiring in",
                   "changes no live rule", "Information, not advice", "[[ADR-0004 one evidence standard]]"):
        assert needle.lower() in text.lower() or needle in text, needle
    assert text.startswith("---")


def test_only_a_confirmed_lesson_can_be_drafted_or_registered(tmp_path):
    lesson = {**_confirmed_lesson(), "stage": "NOMINATED", "decision": None}
    with pytest.raises(G.GovernanceError):
        G.draft(lesson, "SK-0001")
    with pytest.raises(G.GovernanceError):
        G.register(lesson, "SK-0001", "PRE-REGISTERED skip rule SK-0001", "2027-02-01", path=tmp_path / "r.json", audit=AUDIT_OK)


def test_registration_validates_like_the_nightly_job_and_adds_a_shadow_rule(tmp_path):
    p = tmp_path / "rules.json"
    rule = G.register(_confirmed_lesson(), "SK-0001", "PRE-REGISTERED skip rule SK-0001", "2027-02-01", path=p, audit=AUDIT_OK)
    assert rule.effective_status == "SHADOW" and json.loads(p.read_text())["rules"][0]["lesson"] == "L-abc12345"
    assert G.next_rule_id(p) == "SK-0002"
    with pytest.raises(G.GovernanceError, match="already exists"):
        G.register(_confirmed_lesson(), "SK-0001", "PRE-REGISTERED skip rule SK-0001", "2027-02-01", path=p, audit=AUDIT_OK)
    with pytest.raises(SH.ShadowError):                                        # an unregistered note is refused and nothing is written
        G.register(_confirmed_lesson(), "SK-0002", "PRE-REGISTERED skip rule SK-0002", "2027-02-01", path=p, audit=AUDIT_OK)
    assert len(json.loads(p.read_text())["rules"]) == 1


def test_a_draft_is_written_once_and_never_overwritten(tmp_path):
    path = G.write_draft(_confirmed_lesson(), "SK-0001", directory=tmp_path)
    assert path.name == "DRAFT skip rule SK-0001.md" and path.read_text(encoding="utf-8").startswith("---")
    with pytest.raises(G.GovernanceError, match="already exists"):
        G.write_draft(_confirmed_lesson(), "SK-0001", directory=tmp_path)


def test_the_lesson_card_names_the_claim_and_the_evidence():
    text = G.card(_confirmed_lesson())
    assert "ORB trades with vs_market_today = against the market" in text and "-42.0 bp worse" in text and "CONFIRMED" in text
    assert F.DIMENSIONS["vs_market_today"]
