from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import shutil
from pathlib import Path

import pytest

from financial_brain.forward_edge.daily_evidence_bundle import (
    DailyBundleError,
    build_daily_bundle,
    verify_daily_bundle,
)
from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.eod_paper_archive import close_and_archive_paper_session
from financial_brain.kernel.oms import State
from financial_brain.kernel.paper_account import PaperAccount


SESSION = date(2026, 11, 12)
CAPTURE = datetime(2026, 11, 12, 11, 0, tzinfo=timezone.utc)
MARK = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)


def _inputs(tmp_path):
    account_path = tmp_path / "account.jsonl"
    account = PaperAccount(account_path, account_id="bundle-account", initial_cash=Decimal("1000"))
    account.submit_intent(client_id="buy-1", symbol="NSE:INFY", side="BUY", quantity=2,
                          limit=Decimal("100"), reserve=Decimal("200"))
    account.move("buy-1", State.SUBMITTED)
    account.move("buy-1", State.ACCEPTED)
    account.record_fill("buy-1", fill_id="fill-1", quantity=2, price=Decimal("100"))
    eod = close_and_archive_paper_session(
        account=account, session_date=SESSION, captured_at=CAPTURE,
        marks={"NSE:INFY": {"price": "102", "observed_at": MARK.isoformat(),
                             "source_id": "fixture-close", "source_sha256": "a" * 64}},
        archive_dir=tmp_path / "eod",
    )
    runner_path = tmp_path / "runner.jsonl"
    runner = DurableJournal(runner_path)
    runner.append({"t": "SESSION_PLAN", "session_id": "S-2026-11-12",
                   "config": {"exchange_date": SESSION.isoformat()}})
    runner.append({"t": "MARKET_EVENT", "session_id": "S-2026-11-12"})
    runner.append({"t": "DECISION", "session_id": "S-2026-11-12"})
    runner.append({"t": "SESSION_CLOSED", "session_id": "S-2026-11-12",
                   "result": {"status": "PAPER_COMPLETE"}})
    return {"paper_account_archive": Path(eod["archive_path"]), "runner_journal": runner_path}


def test_signed_daily_bundle_reproduces_on_copied_workstation(tmp_path):
    crypto = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")
    private = crypto.Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    trusted = hashlib.sha256(public).hexdigest()
    bundle = tmp_path / "bundle"
    built = build_daily_bundle(
        session_date=SESSION.isoformat(), session_id="S-2026-11-12",
        artifact_paths=_inputs(tmp_path), output_dir=bundle, signing_key=private,
    )
    assert built["verified"] is True
    assert built["signature_status"] == "ED25519_VALID_UNPINNED"
    exported = tmp_path / "second-workstation" / "bundle"
    exported.parent.mkdir()
    shutil.copytree(bundle, exported)
    verified = verify_daily_bundle(exported, trusted_public_key_sha256=trusted)
    assert verified["signature_status"] == "ED25519_TRUSTED"
    assert verified["report_reproduced"] is True
    report = (exported / "operator-report.txt").read_text()
    assert "NAV: 1004" in report
    assert "MARKET_EVENT: 1" in report
    assert "does not claim live execution or profitability" in report


def test_daily_bundle_refuses_input_tamper_and_untrusted_signing_key(tmp_path):
    crypto = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    bundle = tmp_path / "bundle"
    build_daily_bundle(
        session_date=SESSION.isoformat(), session_id="S-2026-11-12",
        artifact_paths=_inputs(tmp_path), output_dir=bundle,
        signing_key=crypto.Ed25519PrivateKey.generate(),
    )
    trusted_wrong_key = hashlib.sha256(b"wrong key").hexdigest()
    with pytest.raises(DailyBundleError, match="trusted key"):
        verify_daily_bundle(bundle, trusted_public_key_sha256=trusted_wrong_key)
    artifact = next((bundle / "inputs").glob("paper_account_archive.json"))
    artifact.write_bytes(artifact.read_bytes() + b" ")
    with pytest.raises(DailyBundleError, match="hash mismatch"):
        verify_daily_bundle(bundle)


def test_hash_only_bundle_is_explicitly_unsigned(tmp_path):
    built = build_daily_bundle(
        session_date=SESSION.isoformat(), session_id="S-2026-11-12",
        artifact_paths=_inputs(tmp_path), output_dir=tmp_path / "unsigned",
    )
    assert built["signature_status"] == "UNSIGNED_HASHED"


def test_existing_bundle_is_not_reused_for_a_different_signing_key(tmp_path):
    crypto = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    artifact_paths = _inputs(tmp_path)
    target = tmp_path / "signed"
    build_daily_bundle(session_date=SESSION.isoformat(), session_id="S-2026-11-12",
                       artifact_paths=artifact_paths, output_dir=target,
                       signing_key=crypto.Ed25519PrivateKey.generate())
    with pytest.raises(DailyBundleError, match="different content"):
        build_daily_bundle(session_date=SESSION.isoformat(), session_id="S-2026-11-12",
                           artifact_paths=artifact_paths, output_dir=target,
                           signing_key=crypto.Ed25519PrivateKey.generate())
