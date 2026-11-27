"""F00 - baseline, evidence firewall and deployment safety (FE-0001, 0002, 0004, 0005, 0006, 0008, 0010, 0052, 0053).

Offline and deterministic. No broker, no credentials, no network (open PRs are never queried here).
"""
from __future__ import annotations

import base64
import hashlib
import json
import shutil
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from financial_brain.forward_edge import approvals as A
from financial_brain.forward_edge import audit, baseline, charter, custody, decision_custody, modes, trials
from financial_brain.forward_edge.modes import ComplianceRecord, Mode, ModeError, SessionMode

REPO = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 10, 3, 0, tzinfo=timezone.utc)
SHA = "a" * 64


def _approval(kind: str = "PROTECTED_TRIAL", *, actor_id: str = "owner:akash", actor_type: str = "human",
              subject: str = "trial-x", scope: str = SHA, adr: str | None = "ADR-0004", valid_from=None,
              valid_to=None) -> A.Approval:
    return A.Approval(kind=kind, subject=subject, scope_sha256=scope, actor_id=actor_id, actor_type=actor_type,
                      approved_at_utc=valid_from or NOW - timedelta(hours=1),
                      expires_at_utc=valid_to or NOW + timedelta(days=1), adr_ref=adr)


def _override_trial_program(monkeypatch: pytest.MonkeyPatch, program_id: str, **changes: Any) -> dict:
    path = REPO / trials.PROGRAM_REL
    document = json.loads(path.read_text(encoding="utf-8"))
    program = next(row for row in document["programs"] if row["program_id"] == program_id)
    program.update(changes)
    original_read_text = Path.read_text

    def read_text(target: Path, *args: Any, **kwargs: Any) -> str:
        if target == path:
            return json.dumps(document)
        if program_id == "Q1-2027" and target == REPO / "docs/adr/0007-q1-2027-trial-protocol.md":
            return "# Q1 2027 trial protocol\nOwner-approved trial protocol test fixture.\n"
        return original_read_text(target, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    if program_id == "Q1-2027":
        original_glob = Path.glob
        fake_adr = REPO / "docs/adr/0007-q1-2027-trial-protocol.md"

        def glob(target: Path, pattern: str):
            if target == REPO / "docs/adr" and pattern == "0007-*.md":
                return [fake_adr]
            return original_glob(target, pattern)

        monkeypatch.setattr(Path, "glob", glob)
    return program


def _trial_identity(trial_name: str, proposed_on: date):
    trial_charter = charter.ExperimentCharter(
        charter_id=trial_name, version=1, strategy_id=trial_name, code_sha256=SHA,
        capital_inr="100000", start_on=proposed_on.isoformat(),
        end_on=(proposed_on + timedelta(days=30)).isoformat(), universe=("INFY",),
        benchmarks=("NIFTY50_TRI",), horizon_sessions=10,
        preregistration=charter.Preregistration(
            hypothesis=f"Frozen {trial_name} rule has positive net return after costs.",
            entry_rule="Enter only on an eligible quote when the predeclared signal and risk checks pass.",
            exit_rule="Exit under the registered stop or horizon; do not revise after cohort start.",
            risk_budget_inr="1000", target_comparisons=("CASH", "NIFTY50_TRI"),
            data_cutoff_on=(proposed_on - timedelta(days=1)).isoformat(),
            minimum_sample_sessions=20,
        ),
    )
    cohort = decision_custody.Cohort(
        cohort_id=f"{trial_name}-cohort", start=trial_charter.start_on, end=trial_charter.end_on)
    schedule = decision_custody.CohortSchedule(trial_charter.sha256(), (cohort,))
    return trial_charter, schedule, trials.protected_trial_scope_sha256(trial_charter, schedule)


# ---------- FE-0001 baseline ----------

def test_baseline_records_git_head_sources_and_missing_prerequisites() -> None:
    record = baseline.capture_baseline(REPO, now=NOW)
    assert record["schema"] == "fb.forward_edge.baseline.v1"
    assert len(record["git"]["head"] or "") == 40
    assert record["git"]["branch"]
    assert record["open_pull_requests"] == "not_queried"
    for rel, info in record["sources"].items():
        if info["present"]:
            assert len(info["sha256"]) == 64, rel
    assert record["sources"]["docs/trials/ledger.jsonl"]["present"] is True
    # credentials are recorded as booleans only
    assert set(record["credentials_present"].values()) <= {True, False}
    assert "custody:trusted_timestamp_roots_not_pinned" in record["missing_prerequisites"]


def test_baseline_write_is_deterministic_for_fixed_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    (root / "data" / "fixture.bin").write_bytes(b"stable fixture")
    git_values = {
        "rev-parse HEAD": "a" * 40,
        "status --porcelain": "",
        "rev-parse --abbrev-ref HEAD": "test-branch",
    }
    monkeypatch.setattr(baseline, "_git", lambda _root, *args: git_values.get(" ".join(args)))
    out = tmp_path / "b.json"
    first = baseline.write_baseline(root, out, now=NOW)
    text = out.read_text(encoding="utf-8")
    again = baseline.write_baseline(root, out, now=NOW)
    assert json.loads(text) == first == again


# ---------- FE-0002 trial exhaustion and append-only ledger ----------

def test_q4_2026_protected_budget_is_exhausted_at_13_of_13() -> None:
    status = trials.budget_status(REPO / trials.LEDGER_REL)
    assert (status.spent, status.budget, status.exhausted) == (13, 13, True)


def test_all_13_registered_q4_trials_remain_fail_with_original_reason_codes() -> None:
    rows = trials.load_rows(REPO / trials.LEDGER_REL)
    registered = [row for row in rows if row.get("kind") == "trial"
                  and "2026-10-01" <= row.get("date", "") <= "2026-12-31"]
    assert len(registered) == 13
    assert all(row.get("verdict") == "FAIL" for row in registered)
    assert all(isinstance(row.get("failed"), list) and row["failed"]
               and all(isinstance(reason, str) and reason for reason in row["failed"])
               for row in registered)


def test_protected_records_match_pinned_digests() -> None:
    assert trials.verify_pins(REPO) == []


def test_pins_agree_with_the_existing_protected_baseline() -> None:
    pinned = json.loads((REPO / trials.PINS_REL).read_text(encoding="utf-8"))["files"]
    existing = json.loads((REPO / "docs" / "audits" / "protected-baseline.json").read_text(encoding="utf-8"))["files"]
    assert set(existing) <= set(pinned)
    for rel, digest in pinned.items():
        if rel in existing:
            assert existing[rel] == digest, rel


def test_protected_pin_normalizes_declared_crlf_checkout_to_canonical_lf(tmp_path: Path) -> None:
    rel = "docs/trials/ledger.jsonl"
    target = tmp_path / rel
    target.parent.mkdir(parents=True)
    canonical = b'{"kind":"trial"}\n'
    target.write_bytes(canonical.replace(b"\n", b"\r\n"))
    expected = hashlib.sha256(canonical).hexdigest()
    pins_dir = tmp_path / "docs/forward-edge"
    pins_dir.mkdir(parents=True)
    (pins_dir / "protected-pins.json").write_text(
        json.dumps({"files": {rel: expected}, "normalize_eol": [rel]}), encoding="utf-8")
    assert trials.verify_pins(tmp_path) == []
    target.write_bytes(canonical + b"unauthorized\n")
    assert trials.verify_pins(tmp_path) == [f"changed: {rel}"]


def test_sealed_historical_record_tampering_is_detected_by_the_runtime_guard(tmp_path: Path) -> None:
    rel = "docs/sources/pattern-catalogue.v1.json"
    target = tmp_path / rel
    target.parent.mkdir(parents=True)
    shutil.copyfile(REPO / rel, target)
    pins_dir = tmp_path / "docs/forward-edge"
    pins_dir.mkdir(parents=True)
    digest = trials.sha256_file(target)
    (pins_dir / "protected-pins.json").write_text(json.dumps({"files": {rel: digest}}), encoding="utf-8")
    assert trials.verify_pins(tmp_path) == []
    target.write_text(target.read_text(encoding="utf-8") + "\nunauthorized edit\n", encoding="utf-8")
    assert trials.verify_pins(tmp_path) == [f"changed: {rel}"]


def test_retrospective_edit_of_the_trial_ledger_is_detected(tmp_path: Path) -> None:
    (tmp_path / "docs/trials").mkdir(parents=True)
    (tmp_path / "docs/forward-edge").mkdir(parents=True)
    src = REPO / trials.LEDGER_REL
    dst = tmp_path / trials.LEDGER_REL
    shutil.copyfile(src, dst)
    pins = {"files": {trials.LEDGER_REL: trials.sha256_file(src)}}
    (tmp_path / trials.PINS_REL).write_text(json.dumps(pins), encoding="utf-8")
    assert trials.verify_pins(tmp_path) == []
    dst.write_text(dst.read_text(encoding="utf-8").replace('"verdict": "FAIL"', '"verdict": "PASS"', 1),
                   encoding="utf-8")
    assert trials.verify_pins(tmp_path) == [f"changed: {trials.LEDGER_REL}"]


def test_new_protected_trial_refused_when_budget_is_spent() -> None:
    with pytest.raises(trials.TrialGovernanceError, match="approved larger explicit budget"):
        trials.assert_new_protected_trial_allowed(REPO, proposed_on=date(2026, 11, 10), trial_name="M9",
                                                  approvals=(), now=NOW, scope_sha256=SHA)


def test_q4_budget_amendment_requires_recorded_capacity_and_matching_owner_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    only_trial = (_approval("PROTECTED_TRIAL", subject="M9", adr="ADR-0004"),)
    with pytest.raises(trials.TrialGovernanceError, match="approved larger explicit budget"):
        trials.assert_new_protected_trial_allowed(REPO, proposed_on=date(2026, 11, 10), trial_name="M9",
                                                  approvals=only_trial, now=NOW, scope_sha256=SHA)
    _override_trial_program(monkeypatch, "Q4-2026", status="AMENDED", budget=14,
                            budget_adr_ref="ADR-0006", authorized_owner_id="owner:akash")
    with pytest.raises(trials.TrialGovernanceError, match="needs a TRIAL_BUDGET_AMENDMENT"):
        trials.assert_new_protected_trial_allowed(REPO, proposed_on=date(2026, 11, 10), trial_name="M9",
                                                  approvals=only_trial, now=NOW, scope_sha256=SHA)
    trial_charter, schedule, scope = _trial_identity("M9", date(2026, 11, 10))
    approvals = (_approval("PROTECTED_TRIAL", subject="M9", adr="ADR-0004", scope=scope),
                 _approval("TRIAL_BUDGET_AMENDMENT", subject="M9", adr="ADR-0006", scope=scope))
    status = trials.assert_new_protected_trial_allowed(REPO, proposed_on=date(2026, 11, 10), trial_name="M9",
                                                        approvals=approvals, now=NOW, scope_sha256=scope,
                                                        charter=trial_charter, cohort_schedule=schedule)
    assert status.spent == 13 and status.remaining == 1


def test_agent_cannot_authorize_a_protected_trial(monkeypatch: pytest.MonkeyPatch) -> None:
    _override_trial_program(monkeypatch, "Q4-2026", status="AMENDED", budget=14,
                            budget_adr_ref="ADR-0006", authorized_owner_id="owner:akash")
    agent = (_approval("PROTECTED_TRIAL", subject="M9", actor_id="agent:research-bot", actor_type="agent",
                       adr="ADR-0004"),
             _approval("TRIAL_BUDGET_AMENDMENT", subject="M9", actor_id="agent:research-bot", actor_type="agent",
                       adr="ADR-0006"))
    with pytest.raises(trials.TrialGovernanceError, match="cannot approve"):
        trials.assert_new_protected_trial_allowed(REPO, proposed_on=date(2026, 11, 10), trial_name="M9",
                                                  approvals=agent, now=NOW, scope_sha256=SHA)


def test_q1_2027_stays_closed_without_an_owner_approved_protocol_and_explicit_budget() -> None:
    with pytest.raises(trials.TrialGovernanceError, match="no owner-approved protocol and explicit trial budget"):
        trials.assert_new_protected_trial_allowed(
            REPO, proposed_on=date(2027, 1, 15), trial_name="M1-2027",
            approvals=(_approval("PROTECTED_TRIAL", subject="M1-2027"),), now=NOW, scope_sha256=SHA)


def test_q1_2027_requires_protocol_amendment_and_trial_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    _override_trial_program(monkeypatch, "Q1-2027", status="ACTIVE", budget=1,
                            protocol_adr_ref="ADR-0007", authorized_owner_id="owner:akash")
    expires = datetime(2027, 1, 16, tzinfo=timezone.utc)
    only_trial = (_approval("PROTECTED_TRIAL", subject="M1-2027", adr="ADR-0007", valid_to=expires),)
    with pytest.raises(trials.TrialGovernanceError, match="needs a Q1_PROTOCOL_AMENDMENT"):
        trials.assert_new_protected_trial_allowed(REPO, proposed_on=date(2027, 1, 15), trial_name="M1-2027",
                                                  approvals=only_trial, now=NOW, scope_sha256=SHA)
    expired = only_trial + (_approval("Q1_PROTOCOL_AMENDMENT", subject="M1-2027", adr="ADR-0007"),)
    with pytest.raises(trials.TrialGovernanceError, match="expired"):
        trials.assert_new_protected_trial_allowed(REPO, proposed_on=date(2027, 1, 15), trial_name="M1-2027",
                                                  approvals=expired, now=NOW, scope_sha256=SHA)
    wrong_owner = only_trial + (_approval("Q1_PROTOCOL_AMENDMENT", subject="M1-2027", adr="ADR-0007",
                                          actor_id="owner:other", valid_to=expires),)
    with pytest.raises(trials.TrialGovernanceError, match="configured owner owner:akash"):
        trials.assert_new_protected_trial_allowed(REPO, proposed_on=date(2027, 1, 15), trial_name="M1-2027",
                                                  approvals=wrong_owner, now=NOW, scope_sha256=SHA)
    trial_charter, schedule, scope = _trial_identity("M1-2027", date(2027, 1, 15))
    approvals = (_approval("PROTECTED_TRIAL", subject="M1-2027", adr="ADR-0007",
                           scope=scope, valid_to=expires),
                 _approval("Q1_PROTOCOL_AMENDMENT", subject="M1-2027", adr="ADR-0007",
                           scope=scope, valid_to=expires))
    status = trials.assert_new_protected_trial_allowed(REPO, proposed_on=date(2027, 1, 15), trial_name="M1-2027",
                                                        approvals=approvals, now=NOW, scope_sha256=scope,
                                                        charter=trial_charter, cohort_schedule=schedule)
    assert status.budget == 1 and status.remaining == 1


def test_protected_trial_requires_charter_and_matching_cohort_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    _override_trial_program(monkeypatch, "Q1-2027", status="ACTIVE", budget=1,
                            protocol_adr_ref="ADR-0007", authorized_owner_id="owner:akash")
    proposed_on = date(2027, 1, 15)
    trial_charter, schedule, scope = _trial_identity("M1-2027", proposed_on)
    expires = datetime(2027, 1, 16, tzinfo=timezone.utc)
    approvals = (_approval("PROTECTED_TRIAL", subject="M1-2027", adr="ADR-0007",
                           scope=scope, valid_to=expires),
                 _approval("Q1_PROTOCOL_AMENDMENT", subject="M1-2027", adr="ADR-0007",
                           scope=scope, valid_to=expires))
    with pytest.raises(trials.TrialGovernanceError, match="exact frozen charter and cohort schedule"):
        trials.assert_new_protected_trial_allowed(REPO, proposed_on=proposed_on, trial_name="M1-2027",
                                                  approvals=approvals, now=NOW, scope_sha256=scope)
    outside = decision_custody.CohortSchedule(trial_charter.sha256(), (
        decision_custody.Cohort("later-cohort", "2027-02-01", "2027-02-10"),))
    outside_scope = trials.protected_trial_scope_sha256(trial_charter, outside)
    outside_approvals = (_approval("PROTECTED_TRIAL", subject="M1-2027", adr="ADR-0007",
                                   scope=outside_scope, valid_to=expires),
                         _approval("Q1_PROTOCOL_AMENDMENT", subject="M1-2027", adr="ADR-0007",
                                   scope=outside_scope, valid_to=expires))
    with pytest.raises(trials.TrialGovernanceError, match="not covered"):
        trials.assert_new_protected_trial_allowed(REPO, proposed_on=proposed_on, trial_name="M1-2027",
                                                  approvals=outside_approvals, now=NOW,
                                                  scope_sha256=outside_scope,
                                                  charter=trial_charter, cohort_schedule=outside)
    wrong_scope_approvals = (_approval("PROTECTED_TRIAL", subject="M1-2027", adr="ADR-0007",
                                       scope=SHA, valid_to=expires),
                             _approval("Q1_PROTOCOL_AMENDMENT", subject="M1-2027", adr="ADR-0007",
                                       scope=SHA, valid_to=expires))
    with pytest.raises(trials.TrialGovernanceError, match="approval scope hash must bind"):
        trials.assert_new_protected_trial_allowed(REPO, proposed_on=proposed_on, trial_name="M1-2027",
                                                  approvals=wrong_scope_approvals, now=NOW, scope_sha256=SHA,
                                                  charter=trial_charter, cohort_schedule=schedule)


def test_protected_trial_scope_rejects_missing_preregistration() -> None:
    trial_charter, _, _ = _trial_identity("M1-2027", date(2027, 1, 15))
    unregistered_design = replace(trial_charter, preregistration=None)
    schedule = decision_custody.CohortSchedule(unregistered_design.sha256(), (
        decision_custody.Cohort("M1-2027-cohort", unregistered_design.start_on, unregistered_design.end_on),))
    with pytest.raises(trials.TrialGovernanceError, match="complete preregistered design"):
        trials.protected_trial_scope_sha256(unregistered_design, schedule)


def test_q1_2027_explicit_budget_is_a_hard_capacity_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    _override_trial_program(monkeypatch, "Q1-2027", status="ACTIVE", budget=1,
                            protocol_adr_ref="ADR-0007", authorized_owner_id="owner:akash")
    rows = trials.load_rows(REPO / trials.LEDGER_REL)
    rows.append({"kind": "trial", "date": "2027-01-10", "name": "already-approved"})
    monkeypatch.setattr(trials, "load_rows", lambda _path: rows)
    expires = datetime(2027, 1, 16, tzinfo=timezone.utc)
    approvals = (_approval("PROTECTED_TRIAL", subject="M1-2027", adr="ADR-0007", valid_to=expires),
                 _approval("Q1_PROTOCOL_AMENDMENT", subject="M1-2027", adr="ADR-0007", valid_to=expires))
    with pytest.raises(trials.TrialGovernanceError, match="budget is exhausted"):
        trials.assert_new_protected_trial_allowed(REPO, proposed_on=date(2027, 1, 15), trial_name="M1-2027",
                                                  approvals=approvals, now=NOW, scope_sha256=SHA)


# ---------- FE-0004 hashable charter ----------

def _charter(**over: Any) -> charter.ExperimentCharter:
    base: dict[str, Any] = dict(charter_id="M1-SLOW-MOMENTUM", version=1, strategy_id="M1", code_sha256=SHA, capital_inr="1000000",
                start_on="2027-01-04", end_on="2027-06-30", universe=("INFY", "TCS"), benchmarks=("NIFTY50_CASH",),
                horizon_sessions=20, parameters=(("lookback", "252"),), prior_sha256=None)
    base.update(over)
    return charter.ExperimentCharter(**base)


def test_charter_hash_is_deterministic_and_sensitive_to_every_field() -> None:
    c = _charter()
    assert c.sha256() == _charter().sha256()
    assert _charter(capital_inr="1000001").sha256() != c.sha256()
    assert _charter(parameters=(("lookback", "126"),)).sha256() != c.sha256()
    assert _charter(universe=("INFY",)).sha256() != c.sha256()


def test_revision_is_a_new_version_with_a_new_clock() -> None:
    v1 = _charter()
    v2 = v1.revise(new_start_on="2027-02-01", parameters=(("lookback", "126"),))
    assert v2.version == 2 and v2.prior_sha256 == v1.sha256()
    assert v2.sha256() != v1.sha256()
    with pytest.raises(charter.CharterError, match="later start"):
        v1.revise(new_start_on="2027-01-04", parameters=(("lookback", "126"),))
    with pytest.raises(charter.CharterError, match="must change"):
        v1.revise(new_start_on="2027-02-01")


@pytest.mark.parametrize(
    "over, message",
    [
        ({"capital_inr": "-1"}, "positive"),
        ({"capital_inr": "abc"}, "decimal"),
        ({"universe": ("TCS", "INFY")}, "sorted"),
        ({"benchmarks": ()}, "benchmark"),
        ({"code_sha256": "xyz"}, "sha256"),
        ({"end_on": "2027-01-01"}, "after start"),
        ({"version": 1, "prior_sha256": SHA}, "version 1"),
        ({"version": 2, "prior_sha256": None}, "version 1"),
    ],
)
def test_malformed_charters_are_rejected(over: dict, message: str) -> None:
    with pytest.raises(charter.CharterError, match=message):
        _charter(**over)


# ---------- FE-0005 / FE-0006 modes fail closed ----------

def test_live_cash_is_unavailable_by_default() -> None:
    with pytest.raises(ModeError, match="permanently disabled"):
        modes.resolve_mode(Mode.LIVE_CASH, now=NOW)


def test_credentials_approvals_or_runtime_flags_cannot_enable_cash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(modes, "PHYSICAL_ORDERS_ENABLED", True)
    monkeypatch.setenv("FB_LIVE_ORDERS", "1")
    monkeypatch.setenv("KITE_API_KEY", "test-key")
    monkeypatch.setenv("KITE_ACCESS_TOKEN", "test-token")
    compliance = ComplianceRecord("broker-x", True, NOW, SHA)
    human = _approval("LIVE_CASH_ENABLE", subject="pilot-1", scope=SHA, adr=None)
    with pytest.raises(ModeError, match="permanently disabled"):
        modes.resolve_mode(Mode.LIVE_CASH, now=NOW, approvals=(human,), compliance=compliance,
                           live_cash_subject="pilot-1", live_cash_scope_sha256=SHA)


def test_live_cash_stays_disabled_with_human_approval_and_compliance(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(modes, "PHYSICAL_ORDERS_ENABLED", True)
    compliance = ComplianceRecord("broker-x", True, NOW, SHA)
    human = _approval("LIVE_CASH_ENABLE", subject="pilot-1", scope=SHA, adr=None)
    with pytest.raises(ModeError, match="permanently disabled"):
        modes.resolve_mode(Mode.LIVE_CASH, now=NOW, approvals=(human,), compliance=compliance,
                           live_cash_subject="pilot-1", live_cash_scope_sha256=SHA)
    agent = _approval("LIVE_CASH_ENABLE", subject="pilot-1", scope=SHA, adr=None,
                      actor_id="agent:exec-bot", actor_type="agent")
    with pytest.raises(ModeError, match="permanently disabled"):
        modes.resolve_mode(Mode.LIVE_CASH, now=NOW, approvals=(agent,), compliance=compliance,
                           live_cash_subject="pilot-1", live_cash_scope_sha256=SHA)


def test_broker_dryrun_needs_verified_compliance() -> None:
    with pytest.raises(ModeError):
        modes.resolve_mode(Mode.BROKER_DRYRUN, now=NOW)
    assert modes.resolve_mode(Mode.SHADOW, now=NOW).mode is Mode.SHADOW


def test_only_live_paper_emits_paper_fills_and_no_mode_sends_physical_orders(
        monkeypatch: pytest.MonkeyPatch) -> None:
    # Deliberately forge the build enable flag: simulation mode authorization must
    # remain nonphysical even under this compromised configuration.
    monkeypatch.setattr(modes, "PHYSICAL_ORDERS_ENABLED", True)
    assert SessionMode(Mode.LIVE_PAPER).may_emit_paper_fill is True
    for mode in (Mode.RESEARCH_REPLAY, Mode.SHADOW, Mode.BROKER_DRYRUN):
        assert SessionMode(mode).may_emit_paper_fill is False
    for mode in Mode:
        assert SessionMode(mode).may_send_physical_order is False


def test_session_mode_is_immutable() -> None:
    session = SessionMode(Mode.SHADOW)
    with pytest.raises(FrozenInstanceError):
        session.mode = Mode.LIVE_CASH  # type: ignore[misc]


# ---------- FE-0008 owner approvals ----------

@pytest.mark.parametrize("actor", ["agent:research", "AI:bot", "llm-trader", "claude", "system", "cron-job", "gpt"])
def test_software_actor_identifiers_cannot_approve(actor: str) -> None:
    approval = _approval(actor_id=actor)
    with pytest.raises(A.ApprovalError, match="cannot approve"):
        A.validate_approval(approval, kind="PROTECTED_TRIAL", subject="trial-x", scope_sha256=SHA, now=NOW)


def test_non_human_actor_type_is_rejected_even_with_a_human_looking_name() -> None:
    approval = _approval(actor_id="akash", actor_type="service")
    with pytest.raises(A.ApprovalError, match="cannot approve"):
        A.validate_approval(approval, kind="PROTECTED_TRIAL", subject="trial-x", scope_sha256=SHA, now=NOW)


def test_expired_future_and_stale_approvals_are_rejected() -> None:
    expired = _approval(valid_from=NOW - timedelta(days=2), valid_to=NOW - timedelta(days=1))
    with pytest.raises(A.ApprovalError, match="expired"):
        A.validate_approval(expired, kind="PROTECTED_TRIAL", subject="trial-x", scope_sha256=SHA, now=NOW)
    future = _approval(valid_from=NOW + timedelta(hours=1))
    with pytest.raises(A.ApprovalError, match="not yet valid"):
        A.validate_approval(future, kind="PROTECTED_TRIAL", subject="trial-x", scope_sha256=SHA, now=NOW)
    stale = _approval(scope="b" * 64)
    with pytest.raises(A.ApprovalError, match="stale"):
        A.validate_approval(stale, kind="PROTECTED_TRIAL", subject="trial-x", scope_sha256=SHA, now=NOW)


def test_wrong_kind_or_subject_and_naive_clock_are_rejected() -> None:
    approval = _approval(subject="trial-x")
    with pytest.raises(A.ApprovalError, match="not"):
        A.validate_approval(approval, kind="CAPITAL", subject="trial-x", scope_sha256=SHA, now=NOW)
    with pytest.raises(A.ApprovalError, match="different subject"):
        A.validate_approval(approval, kind="PROTECTED_TRIAL", subject="trial-y", scope_sha256=SHA, now=NOW)
    with pytest.raises(A.ApprovalError, match="timezone"):
        A.validate_approval(approval, kind="PROTECTED_TRIAL", subject="trial-x", scope_sha256=SHA,
                            now=datetime(2026, 11, 10))


# ---------- FE-0047 .. FE-0053 custody ----------

ROOT_KEY = "root-1"
DECISION = NOW
ENTRY = NOW + timedelta(hours=3)


def _keypair():
    cryptography = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    from cryptography.hazmat.primitives import serialization
    priv = cryptography.Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return priv, base64.b64encode(pub).decode("ascii")


def _receipt(priv, *, digest=SHA, anchored=NOW + timedelta(minutes=5), provider="pinned-tsa", key_id=ROOT_KEY):
    msg = custody.receipt_message(provider, key_id, digest, anchored)
    return custody.Receipt(provider, key_id, digest, anchored, base64.b64encode(priv.sign(msg)).decode("ascii"))


def _classify(receipt, roots, *, digest=SHA, decision=DECISION, entry=ENTRY):
    return custody.classify_custody(manifest_sha256=digest, decision_at_utc=decision, entry_earliest_at_utc=entry,
                                    receipt=receipt, trust_roots=roots)


def test_no_receipt_is_the_honest_local_only_fallback() -> None:
    assert _classify(None, {}) is custody.CustodyStatus.LOCAL_ONLY


def test_independent_receipt_from_a_pinned_root_verifies() -> None:
    priv, pub = _keypair()
    roots = {("pinned-tsa", ROOT_KEY): custody.TrustRoot("pinned-tsa", ROOT_KEY, pub)}
    status = _classify(_receipt(priv), roots)
    assert status is custody.CustodyStatus.VERIFIED
    assert custody.formal_claim_eligible(status)


def test_self_signed_local_receipt_cannot_count_as_independent() -> None:
    priv, pub = _keypair()
    local_key = _receipt(priv, provider="local-dev", key_id="local-dev-key")
    roots = {("pinned-tsa", ROOT_KEY): custody.TrustRoot("pinned-tsa", ROOT_KEY, pub)}
    status = _classify(local_key, roots)
    assert status is custody.CustodyStatus.UNVERIFIED
    assert not custody.formal_claim_eligible(status)


def test_forged_signature_is_unverified() -> None:
    priv, pub = _keypair()
    attacker, _ = _keypair()
    roots = {("pinned-tsa", ROOT_KEY): custody.TrustRoot("pinned-tsa", ROOT_KEY, pub)}
    assert _classify(_receipt(attacker), roots) is custody.CustodyStatus.UNVERIFIED


def test_altered_payload_and_replayed_receipt_are_conflicted() -> None:
    priv, pub = _keypair()
    roots = {("pinned-tsa", ROOT_KEY): custody.TrustRoot("pinned-tsa", ROOT_KEY, pub)}
    receipt = _receipt(priv)
    other_manifest = "c" * 64
    assert _classify(receipt, roots, digest=other_manifest) is custody.CustodyStatus.CONFLICTED


def test_false_clock_receipt_before_decision_is_conflicted() -> None:
    priv, pub = _keypair()
    roots = {("pinned-tsa", ROOT_KEY): custody.TrustRoot("pinned-tsa", ROOT_KEY, pub)}
    early = _receipt(priv, anchored=NOW - timedelta(hours=1))
    assert _classify(early, roots) is custody.CustodyStatus.CONFLICTED


def test_late_receipt_is_late_and_decision_after_entry_is_conflicted() -> None:
    priv, pub = _keypair()
    roots = {("pinned-tsa", ROOT_KEY): custody.TrustRoot("pinned-tsa", ROOT_KEY, pub)}
    late = _receipt(priv, anchored=ENTRY + timedelta(minutes=1))
    assert _classify(late, roots) is custody.CustodyStatus.LATE
    assert _classify(_receipt(priv), roots, decision=ENTRY) is custody.CustodyStatus.CONFLICTED


def test_default_provider_refuses_to_fabricate_a_receipt() -> None:
    with pytest.raises(custody.ProviderNotConfigured, match="NOT_CONFIGURED"):
        custody.UnconfiguredProvider().anchor(SHA)


def test_custody_inputs_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="timezone"):
        custody.classify_custody(manifest_sha256=SHA, decision_at_utc=datetime(2026, 11, 10),
                                 entry_earliest_at_utc=ENTRY, receipt=None, trust_roots={})


# ---------- FE-0010 evidence audit ----------

def test_audit_is_red_on_this_repository_and_never_green_without_custody() -> None:
    report = audit.run_audit(REPO, now=NOW)
    assert report["overall"] == "RED"
    assert report["formal_forward_claim_allowed"] is False
    names = {c["name"]: c["status"] for c in report["checks"]}
    assert names["protected_pins"] == "GREEN"
    assert names["independent_custody"] == "RED"
    assert names["live_cash"] == "GREEN"
    assert any("INDEPENDENT_TIMESTAMP" in b for b in report["blocking"])


def test_current_baseline_is_yellow_until_github_pr_and_protection_state_is_verified() -> None:
    check = audit._baseline(REPO)
    assert check.status == "YELLOW"
    assert "GitHub PR graph or branch protection remains unverified" in check.reason


def test_audit_turns_red_when_a_protected_record_changes(tmp_path: Path) -> None:
    (tmp_path / "docs/trials").mkdir(parents=True)
    (tmp_path / "docs/forward-edge").mkdir(parents=True)
    shutil.copyfile(REPO / trials.LEDGER_REL, tmp_path / trials.LEDGER_REL)
    (tmp_path / trials.PINS_REL).write_text(
        json.dumps({"files": {trials.LEDGER_REL: "0" * 64}}), encoding="utf-8")
    report = audit.run_audit(tmp_path, now=NOW)
    status = {c["name"]: c["status"] for c in report["checks"]}
    assert status["protected_pins"] == "RED"
    assert report["overall"] == "RED"


def test_audit_cli_exit_code_is_nonzero_while_blocked(capsys: pytest.CaptureFixture[str]) -> None:
    code = audit.main(["--repo", str(REPO)])
    assert code == 1
    assert "OVERALL RED" in capsys.readouterr().out
