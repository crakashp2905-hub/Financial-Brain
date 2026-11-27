from __future__ import annotations

import base64
import hashlib
import json
from datetime import date, datetime, timedelta

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from financial_brain.forward_edge.custody import receipt_message
from financial_brain.forward_edge.forward_evidence_gate import evaluate_forward_evidence_gate


def _hash(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _complete_inputs():
    dates = [(date(2026, 1, 1) + timedelta(days=index)).isoformat() for index in range(60)]
    private_key = Ed25519PrivateKey.generate()
    public_key_b64 = base64.b64encode(private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)).decode()
    plan = {
        "plan_id": "forward-proof-v1",
        "frozen_at_utc": "2025-12-30T12:00:00Z",
        "first_eligible_session": dates[0],
        "expected_sessions": dates,
        "minimum_sessions": 60,
        "benchmark_id": "NIFTY50_TRI",
        "strategy_sha256": "c" * 64,
        "cohort_id": "momentum-forward-2026q1",
        "market_calendar_sha256": "d" * 64,
        "trusted_custody_roots": [{"provider_id": "TEST_EXTERNAL", "key_id": "key-v1",
                                   "public_key_b64": public_key_b64}],
    }
    plan["plan_sha256"] = _hash(plan)
    sessions = []
    for day in dates:
        raw_audit_sha256 = _hash({"session": day, "audit": "fixture"})
        decision_at = f"{day}T09:30:00+00:00"
        anchored_at = f"{day}T09:30:30+00:00"
        entry_at = f"{day}T09:31:00+00:00"
        message = receipt_message("TEST_EXTERNAL", "key-v1", raw_audit_sha256,
                                  datetime.fromisoformat(anchored_at))
        custody_receipt = {
            "provider_id": "TEST_EXTERNAL", "key_id": "key-v1",
            "digest_sha256": raw_audit_sha256, "anchored_at_utc": anchored_at,
            "signature_b64": base64.b64encode(private_key.sign(message)).decode(),
        }
        sessions.append({
            "session_date": day, "clean_session": True,
            "raw_audit_sha256": raw_audit_sha256, "custody_receipt": custody_receipt,
            "decision_at_utc": decision_at, "entry_earliest_at_utc": entry_at,
            "strategy_sha256": "c" * 64, "cohort_id": "momentum-forward-2026q1",
            "evidence_tier": "ATTESTED_FORWARD",
        })
    attribution = {"session_dates": dates, "complete": True, "ledger_reconciled": True,
                   "strategy_sha256": "c" * 64, "cohort_id": "momentum-forward-2026q1"}
    attribution["report_sha256"] = _hash(attribution)
    benchmark = {
        "benchmark_id": "NIFTY50_TRI",
        "session_dates": dates,
        "strategy_sha256": "c" * 64,
        "cohort_id": "momentum-forward-2026q1",
        "cash_alpha_lower_bound": 0.001,
        "risk_matched_alpha_lower_bound": 0.0005,
        "independent_receipts_verified": True,
        "evidence_tier": "INDEPENDENT_FORWARD",
    }
    benchmark["report_sha256"] = _hash(benchmark)
    return plan, sessions, attribution, benchmark


def test_gate_is_red_when_prospective_evidence_is_missing():
    report = evaluate_forward_evidence_gate(plan=None, sessions=[], attribution=None, benchmark=None)
    assert report["status"] == "RED"
    assert report["release_blocked"] is True
    assert report["promotion_allowed"] is False
    assert report["trading_authorized"] is False


def test_gate_green_review_required_only_for_exact_clean_frozen_window():
    inputs = _complete_inputs()
    report = evaluate_forward_evidence_gate(
        plan=inputs[0], sessions=inputs[1], attribution=inputs[2], benchmark=inputs[3])
    assert report["status"] == "GREEN_REVIEW_REQUIRED"
    assert report["release_blocked"] is False
    assert report["promotion_allowed"] is False
    assert report["trading_authorized"] is False


def test_dirty_session_or_nonpositive_benchmark_lower_bound_keeps_gate_red():
    plan, sessions, attribution, benchmark = _complete_inputs()
    sessions[12] = sessions[12] | {"clean_session": False}
    benchmark = benchmark | {"risk_matched_alpha_lower_bound": 0.0}
    benchmark["report_sha256"] = _hash({key: value for key, value in benchmark.items()
                                        if key != "report_sha256"})
    report = evaluate_forward_evidence_gate(
        plan=plan, sessions=sessions, attribution=attribution, benchmark=benchmark)
    assert report["status"] == "RED"
    assert report["release_blocked"] is True
    assert report["checks"]["all_expected_sessions_present_and_clean"] is False
    assert report["checks"]["benchmark_superiority_observed"] is False


def test_invalid_external_custody_signature_keeps_gate_red():
    plan, sessions, attribution, benchmark = _complete_inputs()
    sessions[0] = sessions[0] | {
        "custody_receipt": sessions[0]["custody_receipt"] | {"signature_b64": "ZmFrZQ=="}}
    report = evaluate_forward_evidence_gate(
        plan=plan, sessions=sessions, attribution=attribution, benchmark=benchmark)
    assert report["status"] == "RED"
    assert report["checks"]["all_sessions_independently_attested"] is False


def test_report_hashes_are_verified_not_presence_only():
    plan, sessions, attribution, benchmark = _complete_inputs()
    attribution = attribution | {"ledger_reconciled": False}
    report = evaluate_forward_evidence_gate(
        plan=plan, sessions=sessions, attribution=attribution, benchmark=benchmark)
    assert report["status"] == "RED"
    assert report["checks"]["attribution_complete_and_reconciled"] is False
