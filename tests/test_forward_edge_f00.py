"""F00 - baseline, evidence firewall and deployment safety (FE-0001, 0002, 0004, 0005, 0006, 0008, 0010, 0052, 0053).

Offline and deterministic. No broker, no credentials, no network (open PRs are never queried here).
"""
from __future__ import annotations

import base64
import json
import shutil
from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from financial_brain.forward_edge import approvals as A
from financial_brain.forward_edge import audit, baseline, charter, custody, modes, trials
from financial_brain.forward_edge.decision_custody import Cohort, CohortSchedule
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


def _protected_charter(name: str, start: date) -> tuple[charter.ExperimentCharter, CohortSchedule]:
    identity = charter.EvaluationIdentity(
        strategy_code_sha256=SHA, model_code_sha256="b" * 64, data_snapshot_sha256="c" * 64,
        data_rights_id="owner-kite-api", data_rights_sha256="d" * 64,
        market_calendar_id="NSE-CASH-v1", market_calendar_sha256="e" * 64,
        universe_sha256="f" * 64, execution_cost_id="owner-costs-v1", execution_cost_sha256="1" * 64,
        benchmarks=(("NIFTY50_CASH", "2" * 64),),
    )
    charter_value = charter.ExperimentCharter(
        charter_id=name, version=1, strategy_id=name, code_sha256=SHA, capital_inr="1000000",
        start_on=start.isoformat(), end_on=(start + timedelta(days=30)).isoformat(),
        universe=("INFY", "TCS"), benchmarks=("NIFTY50_CASH",), horizon_sessions=5,
        evaluation_identity=identity,
    )
    cohort = Cohort("C1", start.isoformat(), (start + timedelta(days=4)).isoformat())
    return charter_value, CohortSchedule(charter_value.sha256(), (cohort,))


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


def test_baseline_write_is_deterministic_for_fixed_clock(tmp_path: Path) -> None:
    out = tmp_path / "b.json"
    first = baseline.write_baseline(REPO, out, now=NOW)
    text = out.read_text(encoding="utf-8")
    again = baseline.write_baseline(REPO, out, now=NOW)
    assert json.loads(text) == first == again


# ---------- FE-0002 trial exhaustion and append-only ledger ----------

def test_q4_2026_protected_budget_is_exhausted_at_13_of_13() -> None:
    status = trials.budget_status(REPO / trials.LEDGER_REL)
    assert (status.spent, status.budget, status.exhausted) == (13, 13, True)


def test_protected_records_match_pinned_digests() -> None:
    assert trials.verify_pins(REPO) == []


def test_pins_agree_with_the_existing_protected_baseline() -> None:
    pinned = json.loads((REPO / trials.PINS_REL).read_text(encoding="utf-8"))["files"]
    existing = json.loads((REPO / "docs" / "audits" / "protected-baseline.json").read_text(encoding="utf-8"))["files"]
    for rel, digest in pinned.items():
        if rel in existing:
            assert existing[rel] == digest, rel


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
    with pytest.raises(trials.TrialGovernanceError, match="needs a TRIAL_BUDGET_AMENDMENT approval"):
        trials.assert_new_protected_trial_allowed(REPO, proposed_on=date(2026, 11, 10), trial_name="M9",
                                                  approvals=(), now=NOW, scope_sha256=SHA)


def test_new_q4_trial_needs_budget_amendment_as_well_as_trial_approval(tmp_path) -> None:
    only_trial = (_approval("PROTECTED_TRIAL", subject="M9"),)
    with pytest.raises(trials.TrialGovernanceError, match="TRIAL_BUDGET_AMENDMENT"):
        trials.assert_new_protected_trial_allowed(REPO, proposed_on=date(2026, 11, 10), trial_name="M9",
                                                  approvals=only_trial, now=NOW, scope_sha256=SHA)
    both = only_trial + (_approval("TRIAL_BUDGET_AMENDMENT", subject="M9"),)
    spec, cohort_schedule = _protected_charter("M9", date(2026, 11, 10))
    both = tuple(_approval(a.kind, subject="M9", scope=spec.sha256()) for a in both)
    status = trials.assert_new_protected_trial_allowed(REPO, proposed_on=date(2026, 11, 10), trial_name="M9",
                                                       approvals=both, now=NOW, scope_sha256=spec.sha256(), charter=spec,
                                                       cohort_schedule=cohort_schedule,
                                                       charter_registry=charter.CharterRegistry(tmp_path / "m9.jsonl"))
    assert status.exhausted


def test_agent_cannot_authorize_a_protected_trial() -> None:
    agent = (_approval("PROTECTED_TRIAL", subject="M9", actor_id="agent:research-bot", actor_type="agent"),
             _approval("TRIAL_BUDGET_AMENDMENT", subject="M9", actor_id="agent:research-bot", actor_type="agent"))
    with pytest.raises(trials.TrialGovernanceError, match="cannot approve"):
        trials.assert_new_protected_trial_allowed(REPO, proposed_on=date(2026, 11, 10), trial_name="M9",
                                                  approvals=agent, now=NOW, scope_sha256=SHA)


def test_trial_in_2027q1_needs_only_a_protected_trial_approval(tmp_path) -> None:
    spec, cohort_schedule = _protected_charter("M1-2027", date(2027, 1, 15))
    ok = trials.assert_new_protected_trial_allowed(
        REPO, proposed_on=date(2027, 1, 15), trial_name="M1-2027",
        approvals=(_approval("PROTECTED_TRIAL", subject="M1-2027", scope=spec.sha256()),), now=NOW,
        scope_sha256=spec.sha256(), charter=spec, cohort_schedule=cohort_schedule,
        charter_registry=charter.CharterRegistry(tmp_path / "m1.jsonl"))
    assert ok.spent == 13  # the 2026Q4 count is not the 2027Q1 budget


def test_prospective_cohort_cannot_register_without_human_trial_approval(tmp_path) -> None:
    spec, cohort_schedule = _protected_charter("M1-2027", date(2027, 1, 15))
    registry = charter.CharterRegistry(tmp_path / "unapproved.jsonl")
    with pytest.raises(trials.TrialGovernanceError, match="needs a PROTECTED_TRIAL approval"):
        trials.assert_new_protected_trial_allowed(
            REPO, proposed_on=date(2027, 1, 15), trial_name="M1-2027", approvals=(), now=NOW,
            scope_sha256=spec.sha256(), charter=spec, cohort_schedule=cohort_schedule,
            charter_registry=registry)
    assert registry.journal.records() == []


def test_protected_trial_cannot_register_without_full_identity_registry(tmp_path) -> None:
    spec, cohort_schedule = _protected_charter("M9", date(2026, 11, 10))
    with pytest.raises(trials.TrialGovernanceError, match="durable registry"):
        trials.assert_new_protected_trial_allowed(
            REPO, proposed_on=date(2026, 11, 10), trial_name="M9",
            approvals=(_approval("PROTECTED_TRIAL", subject="M9", scope=spec.sha256()),
                       _approval("TRIAL_BUDGET_AMENDMENT", subject="M9", scope=spec.sha256())),
            now=NOW, scope_sha256=spec.sha256(), charter=spec, cohort_schedule=cohort_schedule,
        )


# ---------- FE-0004 hashable charter ----------

def _charter(**over) -> charter.ExperimentCharter:
    base = dict(charter_id="M1-SLOW-MOMENTUM", version=1, strategy_id="M1", code_sha256=SHA, capital_inr="1000000",
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
    with pytest.raises(ModeError, match="disabled by build policy"):
        modes.resolve_mode(Mode.LIVE_CASH, now=NOW)


def test_flipping_the_build_flag_alone_does_not_enable_cash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(modes, "PHYSICAL_ORDERS_ENABLED", True)
    with pytest.raises(ModeError, match="verified broker compliance"):
        modes.resolve_mode(Mode.LIVE_CASH, now=NOW)
    compliance = ComplianceRecord("broker-x", True, NOW, SHA)
    with pytest.raises(ModeError, match="LIVE_CASH_ENABLE"):
        modes.resolve_mode(Mode.LIVE_CASH, now=NOW, compliance=compliance, live_cash_subject="pilot-1",
                           live_cash_scope_sha256=SHA)


def test_live_cash_with_human_approval_and_compliance_is_granted_only_when_all_gates_pass(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(modes, "PHYSICAL_ORDERS_ENABLED", True)
    compliance = ComplianceRecord("broker-x", True, NOW, SHA)
    human = _approval("LIVE_CASH_ENABLE", subject="pilot-1", scope=SHA, adr=None)
    granted = modes.resolve_mode(Mode.LIVE_CASH, now=NOW, approvals=(human,), compliance=compliance,
                                 live_cash_subject="pilot-1", live_cash_scope_sha256=SHA)
    assert granted.mode is Mode.LIVE_CASH
    agent = _approval("LIVE_CASH_ENABLE", subject="pilot-1", scope=SHA, adr=None,
                      actor_id="agent:exec-bot", actor_type="agent")
    with pytest.raises(ModeError, match="cannot approve"):
        modes.resolve_mode(Mode.LIVE_CASH, now=NOW, approvals=(agent,), compliance=compliance,
                           live_cash_subject="pilot-1", live_cash_scope_sha256=SHA)


def test_broker_dryrun_needs_verified_compliance() -> None:
    with pytest.raises(ModeError):
        modes.resolve_mode(Mode.BROKER_DRYRUN, now=NOW)
    assert modes.resolve_mode(Mode.SHADOW, now=NOW).mode is Mode.SHADOW


def test_only_live_paper_emits_paper_fills_and_no_mode_sends_physical_orders() -> None:
    assert SessionMode(Mode.LIVE_PAPER).may_emit_paper_fill is True
    for mode in (Mode.RESEARCH_REPLAY, Mode.SHADOW, Mode.BROKER_DRYRUN):
        assert SessionMode(mode).may_emit_paper_fill is False
    for mode in Mode:
        assert SessionMode(mode).may_send_physical_order is False


def test_session_mode_is_immutable() -> None:
    session = SessionMode(Mode.SHADOW)
    with pytest.raises(FrozenInstanceError, match="cannot assign to field 'mode'"):
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
    # The executable resolver now exists; formal claims still remain blocked by custody.
    assert names["promotion_metric"] == "GREEN"
    assert names["live_cash"] == "GREEN"
    assert any("INDEPENDENT_TIMESTAMP" in b for b in report["blocking"])


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


def test_ledger_pin_is_stable_across_line_ending_conversion(tmp_path: Path) -> None:
    """Regression: a Windows autocrlf checkout rewrites the ledger to CRLF; the pin must still verify (FE-0148)."""
    (tmp_path / "docs/trials").mkdir(parents=True)
    (tmp_path / "docs/forward-edge").mkdir(parents=True)
    lf = (REPO / trials.LEDGER_REL).read_bytes().replace(b"\r\n", b"\n")
    (tmp_path / trials.LEDGER_REL).write_bytes(lf.replace(b"\n", b"\r\n"))
    pins = {"files": {trials.LEDGER_REL: trials.sha256_file(REPO / trials.LEDGER_REL, normalize_eol=True)},
            "normalize_eol": [trials.LEDGER_REL]}
    (tmp_path / trials.PINS_REL).write_text(json.dumps(pins), encoding="utf-8")
    assert trials.verify_pins(tmp_path) == []
    (tmp_path / trials.LEDGER_REL).write_bytes(lf.replace(b"FAIL", b"PASS", 1).replace(b"\n", b"\r\n"))
    assert trials.verify_pins(tmp_path) == [f"changed: {trials.LEDGER_REL}"]
