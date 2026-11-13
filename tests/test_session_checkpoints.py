from datetime import datetime, timezone

import pytest

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.session_checkpoints import (
    CheckpointError,
    create_checkpoint,
    verify_checkpoint,
    verify_checkpoint_pair,
)


def _write_sources(tmp_path):
    events = DurableJournal(tmp_path / "events.jsonl")
    events.append({"t": "QUOTE", "instrument": "NSE:INFY", "price": "100"})
    account = DurableJournal(tmp_path / "account.jsonl")
    account.append({"t": "ACCOUNT_OPEN", "cash": "10000"})
    return events, account


def _create(tmp_path, events, account, phase, at):
    return create_checkpoint(
        checkpoint_journal_path=tmp_path / "checkpoints.jsonl", session_id="session-1",
        exchange_date="2026-11-12", phase=phase, event_journal_path=events.path,
        account_journal_path=account.path, captured_at_utc=at, writers_quiesced=True,
    )


def test_atomic_start_end_checkpoint_pair_tracks_both_journal_prefixes(tmp_path):
    events, account = _write_sources(tmp_path)
    start = _create(tmp_path, events, account, "START_OF_DAY",
                    datetime(2026, 11, 12, 3, 45, tzinfo=timezone.utc))
    events.append({"t": "DECISION", "side": "BUY"})
    account.append({"t": "FILL", "fill_id": "fill-1", "quantity": 1})
    end = _create(tmp_path, events, account, "END_OF_DAY",
                  datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc))

    assert start["event_journal"]["sequence"] == 1
    assert end["event_journal"]["sequence"] == 2
    assert start["account_journal"]["sequence"] == 1
    assert end["account_journal"]["sequence"] == 2
    assert verify_checkpoint_pair(start, end, event_journal_path=events.path,
                                  account_journal_path=account.path)


def test_checkpoint_requires_quiesced_writers_and_preserves_torn_source(tmp_path):
    events, account = _write_sources(tmp_path)
    with pytest.raises(CheckpointError, match="writers must be quiesced"):
        create_checkpoint(
            checkpoint_journal_path=tmp_path / "checkpoints.jsonl", session_id="session-1",
            exchange_date="2026-11-12", phase="START_OF_DAY", event_journal_path=events.path,
            account_journal_path=account.path,
            captured_at_utc=datetime(2026, 11, 12, 3, 45, tzinfo=timezone.utc), writers_quiesced=False,
        )
    events.append_torn({"t": "QUOTE", "instrument": "NSE:INFY"})
    before = events.path.read_bytes()
    with pytest.raises(CheckpointError, match="torn tail"):
        _create(tmp_path, events, account, "START_OF_DAY",
                datetime(2026, 11, 12, 3, 45, tzinfo=timezone.utc))
    assert events.path.read_bytes() == before


def test_checkpoint_verification_rejects_changed_journal_prefix(tmp_path):
    events, account = _write_sources(tmp_path)
    checkpoint = _create(tmp_path, events, account, "START_OF_DAY",
                         datetime(2026, 11, 12, 3, 45, tzinfo=timezone.utc))
    assert verify_checkpoint(checkpoint, event_journal_path=events.path,
                             account_journal_path=account.path)
    raw = events.path.read_bytes().replace(b'"price":"100"', b'"price":"101"')
    events.path.write_bytes(raw)
    assert not verify_checkpoint(checkpoint, event_journal_path=events.path,
                                 account_journal_path=account.path)


def test_checkpoint_phase_retry_returns_original_snapshot_after_restart(tmp_path):
    events, account = _write_sources(tmp_path)
    original = _create(tmp_path, events, account, "START_OF_DAY",
                       datetime(2026, 11, 12, 3, 45, tzinfo=timezone.utc))
    events.append({"t": "DECISION", "side": "BUY"})
    retry = _create(tmp_path, events, account, "START_OF_DAY",
                    datetime(2026, 11, 12, 3, 46, tzinfo=timezone.utc))
    assert retry == original


def test_checkpoint_journal_cannot_alias_source_journal(tmp_path):
    events, account = _write_sources(tmp_path)
    with pytest.raises(CheckpointError, match="distinct paths"):
        create_checkpoint(
            checkpoint_journal_path=events.path, session_id="session-1",
            exchange_date="2026-11-12", phase="START_OF_DAY", event_journal_path=events.path,
            account_journal_path=account.path,
            captured_at_utc=datetime(2026, 11, 12, 3, 45, tzinfo=timezone.utc), writers_quiesced=True,
        )
