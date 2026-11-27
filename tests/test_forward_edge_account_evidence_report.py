from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from financial_brain.forward_edge.account_evidence_report import (
    AccountActivity,
    ActivityClass,
    build_account_evidence_report,
    verify_account_evidence_report,
)
from financial_brain.forward_edge.custody import Receipt, TrustRoot, receipt_message


T0 = datetime(2026, 11, 27, 4, tzinfo=timezone.utc)


def _broker_activity(private, digest="d" * 64):
    anchored = T0 + timedelta(seconds=5)
    receipt = Receipt("custodian", "key-1", digest, anchored,
                      base64.b64encode(private.sign(receipt_message(
                          "custodian", "key-1", digest, anchored))).decode())
    return AccountActivity("broker-1", ActivityClass.BROKER_ACTIVITY, 10.0,
                           digest, digest, T0, T0 + timedelta(seconds=10), receipt,
                           "broker-order-991")


def test_report_separates_research_paper_hypothesis_and_verified_broker_activity():
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes_raw()
    roots = {("custodian", "key-1"): TrustRoot(
        "custodian", "key-1", base64.b64encode(public).decode())}
    report = build_account_evidence_report(activities=(
        AccountActivity("research-1", ActivityClass.RESEARCH, 100.0, "a" * 64,
                        "a" * 64, T0, T0 + timedelta(seconds=10)),
        AccountActivity("paper-1", ActivityClass.PAPER_FILL, 5.0, "b" * 64,
                        "b" * 64, T0, T0 + timedelta(seconds=10)),
        AccountActivity("scenario-1", ActivityClass.HYPOTHETICAL, -100.0, "c" * 64,
                        "c" * 64, T0, T0 + timedelta(seconds=10)),
        _broker_activity(private),
    ), trust_roots=roots)
    assert report["verified_account_activity_net_pnl_inr"] == 15
    assert report["class_totals"]["RESEARCH"]["net_pnl_inr"] == 100
    assert report["class_totals"]["HYPOTHETICAL"]["net_pnl_inr"] == -100
    assert report["records"][3]["disposition"] == "BROKER_VERIFIED_ACCOUNT_ACTIVITY"
    assert report["records"][3]["custody_status"] == "VERIFIED"
    assert verify_account_evidence_report(report) is True


def test_untrusted_or_wrong_manifest_broker_activity_is_excluded_and_tampering_fails():
    private = Ed25519PrivateKey.generate()
    roots = {("custodian", "key-1"): TrustRoot(
        "custodian", "key-1", base64.b64encode(private.public_key().public_bytes_raw()).decode())}
    valid = _broker_activity(private)
    unsigned = AccountActivity("broker-2", ActivityClass.BROKER_ACTIVITY, 50.0,
                               "e" * 64, "e" * 64, T0, T0 + timedelta(seconds=10), None,
                               "broker-order-992")
    mismatched = AccountActivity("broker-3", ActivityClass.BROKER_ACTIVITY, 75.0,
                                 "f" * 64, "1" * 64, T0, T0 + timedelta(seconds=10),
                                 valid.receipt, "broker-order-993")
    report = build_account_evidence_report(activities=(valid, unsigned, mismatched), trust_roots=roots)
    assert report["records"][1]["disposition"] == "BROKER_ACTIVITY_NOT_VERIFIED_EXCLUDED"
    assert report["records"][2]["custody_status"] == "CONFLICTED"
    assert report["verified_account_activity_net_pnl_inr"] == 10
    assert verify_account_evidence_report(report) is True
    report["records"][0]["net_pnl_inr"] = 999
    assert verify_account_evidence_report(report) is False
