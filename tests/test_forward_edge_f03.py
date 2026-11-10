"""F03 - market-session capture and data integrity (FE-0033 .. FE-0044). Offline fixtures only."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from financial_brain.forward_edge import market_data as M

T0 = datetime(2027, 1, 4, 9, 15, tzinfo=timezone(timedelta(hours=5, minutes=30)))


def _ev(kind="QUOTE", **over) -> M.NormalizedEvent:
    base = dict(kind=kind, instrument="INFY", event_utc=T0, received_utc=T0 + timedelta(milliseconds=40),
                available_utc=T0 + timedelta(milliseconds=50), payload=(("bid", "1500.00"), ("ask", "1500.05")))
    base.update(over)
    return M.NormalizedEvent(**base)


# ---- FE-0033 normalized clocks ----

def test_event_keeps_all_four_clocks_in_utc_order() -> None:
    e = _ev()
    assert e.event_utc <= e.received_utc <= e.available_utc
    assert e.event_utc.tzinfo is not None


def test_impossible_clock_order_and_naive_times_are_rejected() -> None:
    with pytest.raises(M.DataError, match="clocks"):
        _ev(received_utc=T0 - timedelta(seconds=1))
    with pytest.raises(M.DataError, match="timezone"):
        _ev(event_utc=datetime(2027, 1, 4, 9, 15))
    with pytest.raises(M.DataError, match="unknown kind"):
        _ev(kind="RUMOUR")


def test_decision_sees_only_what_was_available_then() -> None:
    e = _ev()
    assert M.decision_visible(e, e.available_utc) is True
    assert M.decision_visible(e, e.event_utc) is False  # stamped at the decision time but not yet available


def test_content_hash_changes_when_any_field_changes() -> None:
    assert _ev().content_sha256() == _ev().content_sha256()
    assert _ev(payload=(("bid", "1500.01"), ("ask", "1500.05"))).content_sha256() != _ev().content_sha256()


# ---- FE-0034 calendar ----

def _calendar(tmp_path: Path, holidays: list[str]) -> M.Calendar:
    p = tmp_path / "cal.json"
    p.write_text(json.dumps({"version": "test-1", "holidays": holidays}), encoding="utf-8")
    return M.load_calendar(p)


def test_next_session_skips_weekends_and_holidays(tmp_path: Path) -> None:
    cal = _calendar(tmp_path, ["2027-01-26"])        # a holiday on a Tuesday
    assert cal.next_tradable(date(2027, 1, 22)) == date(2027, 1, 25)   # Friday -> Monday
    assert cal.next_tradable(date(2027, 1, 25)) == date(2027, 1, 27)   # Mon -> Wed, Tue is a holiday
    assert cal.version == "test-1"


def test_calendar_needs_a_version_and_holiday_list(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"holidays": []}), encoding="utf-8")
    with pytest.raises(M.DataError):
        M.load_calendar(p)


# ---- FE-0035 offline fixture feed ----

def test_fixture_feed_replays_in_deterministic_order(tmp_path: Path) -> None:
    rows = [
        {"kind": "QUOTE", "instrument": "B", "event_utc": "2027-01-04T03:45:00+00:00",
         "received_utc": "2027-01-04T03:45:00.010+00:00", "available_utc": "2027-01-04T03:45:00.020+00:00",
         "payload": {"bid": 10}},
        {"kind": "QUOTE", "instrument": "A", "event_utc": "2027-01-04T03:45:00+00:00",
         "received_utc": "2027-01-04T03:45:00+00:00", "available_utc": "2027-01-04T03:45:00+00:00"},
    ]
    p = tmp_path / "fx.json"
    p.write_text(json.dumps(rows), encoding="utf-8")
    events = M.fixture_feed(p)
    assert [e.instrument for e in events] == ["A", "B"]


# ---- FE-0036 append-only archive ----

def test_archive_is_hash_chained_and_detects_edits(tmp_path: Path) -> None:
    arc = M.QuoteArchive(tmp_path / "quotes.jsonl")
    arc.append(_ev())
    arc.append(_ev(payload=(("bid", "1501.00"), ("ask", "1501.05"))))
    assert arc.verify() is True
    lines = (tmp_path / "quotes.jsonl").read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace('"content": "', '"content": "0')
    (tmp_path / "quotes.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert M.QuoteArchive(tmp_path / "quotes.jsonl").verify() is False


def test_dropped_updates_are_counted_not_hidden(tmp_path: Path) -> None:
    arc = M.QuoteArchive(tmp_path / "q.jsonl")
    arc.record_drop()
    arc.record_drop()
    assert arc.dropped_updates == 2


# ---- FE-0037 abstain on bad feed, never forward-fill ----

def test_stale_gapped_or_disconnected_feed_abstains() -> None:
    now = T0 + timedelta(seconds=30)
    ok, reasons = M.entry_allowed(M.FeedHealth(T0 + timedelta(seconds=29), 0, 0, 0), now)
    assert ok and reasons == []
    ok, reasons = M.entry_allowed(M.FeedHealth(T0, 0, 0, 0), now)
    assert not ok and reasons == ["STALE_QUOTE"]
    ok, reasons = M.entry_allowed(M.FeedHealth(None, 2, 1, 3), now)
    assert not ok and set(reasons) == {"NO_QUOTES", "QUOTE_GAP", "DISCONNECTED", "DROPPED_UPDATES"}


# ---- FE-0038 decision snapshot ----

def test_decision_snapshot_hash_covers_every_input() -> None:
    base = M.DecisionSnapshot(T0, (("bars", "a" * 64), ("calendar", "b" * 64)))
    changed = M.DecisionSnapshot(T0, (("bars", "a" * 64), ("calendar", "c" * 64)))
    assert base.sha256() == M.DecisionSnapshot(T0, (("bars", "a" * 64), ("calendar", "b" * 64))).sha256()
    assert base.sha256() != changed.sha256()


# ---- FE-0039 instrument master ----

@pytest.mark.parametrize(
    "inst, as_of, expected",
    [
        (M.Instrument("INE1", "INFY", 1, "0.05", "ACTIVE", True), date(2027, 1, 4), []),
        (M.Instrument("INE1", "INFY", 1, "0.05", "SUSPENDED", True), date(2027, 1, 4), ["NOT_TRADABLE_STATUS"]),
        (M.Instrument("INE1", "INFY", 1, "0.05", "DELISTED", True), date(2027, 1, 4), ["NOT_TRADABLE_STATUS"]),
        (M.Instrument("INE1", "X", 1, "0.05", "ACTIVE", True, date(2027, 1, 1)), date(2027, 1, 4), ["EXPIRED"]),
        (M.Instrument("INE1", "X", 0, "0.05", "ACTIVE", False), date(2027, 1, 4), ["BAD_LOT", "NO_DATA_RIGHTS"]),
    ],
)
def test_instrument_gate_emits_reason_codes(inst, as_of, expected) -> None:
    assert M.instrument_gate(inst, as_of) == expected


# ---- FE-0040 options executable quotes ----

def test_option_mid_without_two_sided_quotes_is_not_executable() -> None:
    assert M.option_quote_executable(None, 12.0, 100, 100) == (False, "ONE_SIDED_OR_MID_ONLY")
    assert M.option_quote_executable(11.0, 12.0, 0, 100) == (False, "NO_DEPTH")
    assert M.option_quote_executable(12.0, 11.0, 100, 100) == (False, "CROSSED_OR_INVALID")
    assert M.option_quote_executable(11.0, 12.0, 100, 100) == (True, "EXECUTABLE")


# ---- FE-0041 close reconciliation is versioned, not silent ----

def test_close_divergence_is_recorded_not_corrected() -> None:
    rec = M.reconcile_close(1500.0, 1500.5)
    assert rec["status"] == "DIVERGED" and rec["live_close"] == 1500.0 and rec["difference"] == 0.5
    assert M.reconcile_close(1500.0, 1500.0)["status"] == "MATCH"


# ---- FE-0042 deterministic reason codes ----

def test_data_gate_reason_codes_are_deterministic_and_complete() -> None:
    codes = M.data_gate(age_s=9, spread_bps=80, volume=0, circuit_hit=True, frozen=True, auction_seen=False)
    assert codes == ["CIRCUIT", "FREEZE", "MISSING_AUCTION", "STALE", "WIDE_SPREAD", "ZERO_VOLUME"]
    assert M.data_gate(age_s=1, spread_bps=5, volume=10, circuit_hit=False, frozen=False, auction_seen=True) == []


# ---- FE-0043 prefix invariance and future perturbation ----

def test_feature_is_prefix_invariant_and_ignores_the_future() -> None:
    ts = [T0 + timedelta(minutes=i) for i in range(12)]
    closes = [100.0 + i for i in range(12)]
    t = ts[7]
    base = M.feature_at(closes, ts, t)
    prefix = M.feature_at(closes[:8], ts[:8], t)
    assert base == prefix
    perturbed = closes[:8] + [9999.0] * 4          # the future changes, the past does not
    assert M.feature_at(perturbed, ts, t) == base


# ---- FE-0044 coverage ----

def test_coverage_is_reported_by_session_name_and_notional() -> None:
    cov = M.coverage({"d1": 10, "d2": 0}, {"A": True, "B": False, "C": True},
                     [(100.0, True), (300.0, False)])
    assert cov == {"sessions_pct": 50.0, "names_pct": 66.6667, "intent_notional_pct": 25.0}
