from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from financial_brain.forward_edge.durable import JournalCorrupt
from financial_brain.forward_edge.paper_execution_adapter import MarketableQuoteSimulator
from financial_brain.forward_edge.raw_quote_archive import RawArchiveError, RawQuoteArchive
from financial_brain.ingest.kite_live import forward_events


NOW = datetime(2026, 11, 12, 4, 0, tzinfo=timezone.utc)
QUOTE = {
    "timestamp": "2026-11-12 09:30:01", "last_price": 100.0,
    "depth": {"buy": [{"price": 99.9, "quantity": 15}],
              "sell": [{"price": 100.1, "quantity": 12}]},
}


def _attestation():
    return {"approved": True, "authorization_id": "test-retention-auth",
            "controller": "synthetic-test-owner", "purpose": "LOCAL_PERSONAL_PAPER_RESEARCH",
            "evidence_sha256": "d" * 64, "approved_at_utc": NOW.isoformat()}


def test_raw_archive_requires_explicit_retention_attestation(tmp_path):
    with pytest.raises(RawArchiveError, match="explicit approved"):
        RawQuoteArchive(tmp_path / "raw.jsonl", retention_attestation={"approved": False})


def test_kite_event_links_durable_raw_quote_record_under_authorized_archive(tmp_path):
    archive = RawQuoteArchive(tmp_path / "raw.jsonl", retention_attestation=_attestation())
    event, = forward_events(["INFY"], quote_fn=lambda *_: {"NSE:INFY": QUOTE},
                            received_at=NOW + timedelta(seconds=2),
                            ingested_at=NOW + timedelta(seconds=3),
                            raw_archive=archive)
    payload = dict(event.payload)
    rows = archive.records()
    raw_record = rows[0]

    assert archive.verify()
    assert raw_record["t"] == "RAW_QUOTE_CAPTURE"
    assert json.loads(raw_record["payload_json"]) == QUOTE
    assert raw_record["payload_sha256"] == payload["raw_sha256"]
    assert payload["raw_archive_seq"] == "1"
    assert payload["raw_archive_sha256"] == archive.journal.head


def test_simulated_fill_receipt_points_through_event_to_raw_quote_archive(tmp_path):
    archive = RawQuoteArchive(tmp_path / "raw.jsonl", retention_attestation=_attestation())
    quote = {**QUOTE, "timestamp": "2026-11-12 09:30:01"}
    event, = forward_events(["INFY"], quote_fn=lambda *_: {"NSE:INFY": quote},
                            received_at=NOW + timedelta(seconds=1),
                            ingested_at=NOW + timedelta(seconds=2), raw_archive=archive)
    decision = {"instrument": "NSE:INFY", "side": "BUY", "quantity": 2,
                "decision_at_utc": NOW.isoformat()}
    receipt = MarketableQuoteSimulator({"NSE:INFY": Decimal("0.05")},
                                      fee_schedule_sha256="a" * 64)(decision, "intent-raw", [event])

    assert receipt["status"] == "FILLED"
    fill = receipt["fills"][0]
    assert fill["quote_event_sha256"] == event.content_sha256()
    event_payload = dict(event.payload)
    archived = archive.records()[0]
    assert event_payload["raw_archive_sha256"] == archive.journal.head
    assert archived["payload_sha256"] == event_payload["raw_sha256"]


def test_raw_archive_hash_chain_rejects_record_edits(tmp_path):
    path = tmp_path / "raw.jsonl"
    archive = RawQuoteArchive(path, retention_attestation=_attestation())
    archive.append_quote("NSE:INFY", QUOTE, received_at=NOW, available_at=NOW)
    text = path.read_text(encoding="utf-8").replace("100.0", "101.0")
    path.write_text(text, encoding="utf-8")

    with pytest.raises(JournalCorrupt):
        RawQuoteArchive(path, retention_attestation=_attestation())
