from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from financial_brain.forward_edge.durable import JournalCorrupt
from financial_brain.forward_edge.market_data import NormalizedEvent
from financial_brain.forward_edge.paper_execution_adapter import MarketableQuoteSimulator
from financial_brain.forward_edge.raw_quote_archive import RawArchiveError, RawQuoteArchive
from financial_brain.ingest.kite_live import forward_events, historical_bar_events


NOW = datetime(2026, 11, 12, 4, 0, tzinfo=timezone.utc)
QUOTE = {
    "timestamp": "2026-11-12 09:30:01", "last_price": 100.0,
    "depth": {"buy": [{"price": 99.9, "quantity": 15}],
              "sell": [{"price": 100.1, "quantity": 12}]},
}


def _attestation(*, kinds=None, sources=None):
    attestation = {"approved": True, "authorization_id": "test-retention-auth",
                   "controller": "synthetic-test-owner", "purpose": "LOCAL_PERSONAL_PAPER_RESEARCH",
                   "evidence_sha256": "d" * 64, "approved_at_utc": NOW.isoformat()}
    if kinds is not None:
        attestation["permitted_event_kinds"] = kinds
    if sources is not None:
        attestation["permitted_sources"] = sources
    return attestation


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
    assert receipt["quote_inputs"][0]["raw_archive_seq"] == "1"
    assert receipt["used_quote_input_id"] == fill["quote_input_id"]
    assert fill["quote_input_id"].startswith("raw:1:")
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


def test_market_archive_round_trips_quotes_ticks_and_bars_by_availability(tmp_path):
    archive = RawQuoteArchive(
        tmp_path / "market.jsonl",
        retention_attestation=_attestation(
            kinds=["QUOTE", "TICK", "BAR"],
            sources=["kite_connect", "exchange_trade_feed", "kite_historical"],
        ),
    )
    quote = NormalizedEvent("QUOTE", "NSE:INFY", NOW, NOW, NOW + timedelta(milliseconds=10),
                            (("ask", "100.1"), ("bid", "99.9")))
    tick = NormalizedEvent("TICK", "NSE:INFY", NOW + timedelta(milliseconds=20),
                           NOW + timedelta(milliseconds=25), NOW + timedelta(milliseconds=30),
                           (("price", "100.0"), ("quantity", "5")))
    # Historical bar is received later, so it must not leak into an earlier replay.
    bar = NormalizedEvent("BAR", "NSE:INFY", NOW - timedelta(minutes=1),
                          NOW + timedelta(seconds=2), NOW + timedelta(seconds=2),
                          (("close", "100"), ("interval", "minute"), ("open", "99")))
    for event, source in ((quote, "kite_connect"), (tick, "exchange_trade_feed"),
                          (bar, "kite_historical")):
        archive.append_market_event(event, source=source)

    assert archive.verify()
    early = archive.replay_events(as_of=NOW + timedelta(seconds=1))
    full = archive.replay_events(as_of=NOW + timedelta(seconds=3))
    assert [event.kind for event in early] == ["QUOTE", "TICK"]
    assert [event.kind for event in full] == ["QUOTE", "TICK", "BAR"]
    assert [event.content_sha256() for event in full] == [
        quote.content_sha256(), tick.content_sha256(), bar.content_sha256()
    ]


def test_market_archive_requires_kind_and_source_retention_scope(tmp_path):
    archive = RawQuoteArchive(tmp_path / "market.jsonl", retention_attestation=_attestation())
    event = NormalizedEvent("TICK", "NSE:INFY", NOW, NOW, NOW, (("price", "100"),))
    with pytest.raises(RawArchiveError, match="does not permit TICK"):
        archive.append_market_event(event, source="exchange_trade_feed")
    assert archive.records() == []


def test_historical_kite_bars_are_available_only_after_retrieval_and_archive_with_rights(tmp_path):
    fetched_at = NOW + timedelta(days=5)
    archive = RawQuoteArchive(
        tmp_path / "bars.jsonl",
        retention_attestation=_attestation(kinds=["BAR"], sources=["kite_historical"]),
    )
    calls = []

    def candle_fn(token, start, as_of, **kwargs):
        calls.append((token, start, as_of, kwargs))
        return [{"ts": "2026-11-12 09:30:00", "open": 99, "high": 101, "low": 98,
                 "close": 100, "volume": 1234, "oi": None}]

    events = historical_bar_events("NSE:INFY", 408065, "2026-11-12", "2026-11-12",
                                   fetched_at=fetched_at, candles_fn=candle_fn, raw_archive=archive)

    assert calls[0][0] == 408065
    assert calls[0][3]["interval"] == "minute"
    assert events[0].kind == "BAR"
    assert events[0].available_utc == fetched_at
    assert archive.replay_events(as_of=NOW) == []
    assert archive.replay_events(as_of=fetched_at) == events
    assert archive.verify()


def test_historical_kite_bars_reject_invalid_ohlc(tmp_path):
    with pytest.raises(ValueError, match="OHLCV values"):
        historical_bar_events(
            "NSE:INFY", 408065, "2026-11-12", "2026-11-12", fetched_at=NOW,
            candles_fn=lambda *_args, **_kwargs: [{"ts": "2026-11-12 09:30:00", "open": 99,
                                                    "high": 98, "low": 97, "close": 98,
                                                    "volume": 1}],
        )
