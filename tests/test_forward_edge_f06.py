"""F06 - session automation and reliability (FE-0069 .. FE-0078). Offline, injected clock."""
from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from financial_brain.forward_edge import session_ops as S
from financial_brain.forward_edge.market_data import Calendar

IST = S.IST


def _t(h: int, m: int, day: int = 4) -> datetime:
    return datetime(2027, 1, day, h, m, tzinfo=IST)


CAL = Calendar("t", frozenset({date(2027, 1, 26)}))
ALL_OK = {k: True for k in S.REQUIRED_PREFLIGHT}


# ---- FE-0069 ----

def test_blocked_preflight_leaves_no_intents_but_writes_a_report() -> None:
    bad = {**ALL_OK, "feeds": False}
    report = S.run_preflight(bad, _t(9, 0))
    assert report.ok is False and report.failures == ("feeds",)
    assert S.allowed_intents(report, [{"instrument": "INFY"}]) == []
    assert S.allowed_intents(S.run_preflight(ALL_OK, _t(9, 0)), [{"instrument": "INFY"}]) == [{"instrument": "INFY"}]


def test_a_missing_check_counts_as_failed_not_as_passed() -> None:
    partial = {k: True for k in S.REQUIRED_PREFLIGHT if k != "broker_disabled"}
    assert S.run_preflight(partial, _t(9, 0)).failures == ("broker_disabled",)


# ---- FE-0070 ----

def test_supervisor_states_follow_the_injected_clock_and_calendar() -> None:
    sup = S.SessionSupervisor(CAL)
    assert sup.state(_t(9, 0)) == "PREOPEN"
    assert sup.state(_t(9, 15)) == "OPEN"
    assert sup.state(_t(9, 40)) == "LATE_START"
    assert sup.state(_t(15, 30)) == "CLOSED"
    assert sup.state(_t(10, 0, day=26)) == "HOLIDAY"
    assert sup.state(_t(10, 0, day=9)) == "HOLIDAY"  # Saturday


# ---- FE-0071 ----

def test_range_freezes_only_from_a_complete_window() -> None:
    start, end = _t(9, 15), _t(9, 20)
    bars = [(start + timedelta(minutes=i), 100.0 + i, 99.0 - i) for i in range(5)]
    assert S.freeze_opening_range(bars, start, end, _t(9, 17), minutes=5)["status"] == "INCOMPLETE"
    assert S.freeze_opening_range(bars[:4], start, end, _t(9, 21), minutes=5)["reason"] == "MISSING_MINUTES"
    frozen = S.freeze_opening_range(bars, start, end, _t(9, 21), minutes=5)
    assert frozen["status"] == "FROZEN" and frozen["high"] == 104.0 and frozen["low"] == 95.0


def test_restart_cannot_turn_historical_data_into_live_knowledge() -> None:
    end = _t(9, 20)
    assert S.usable_after_restart(_t(9, 21), end, _t(10, 0)) is True
    assert S.usable_after_restart(None, end, _t(10, 0)) is False       # no live freeze record: reconstruction is not usable
    assert S.usable_after_restart(_t(9, 10), end, _t(10, 0)) is False  # "frozen" before the window ended is invalid


# ---- FE-0072 ----

def test_journal_replay_reproduces_the_identical_hash_and_detects_edits() -> None:
    j = S.DecisionJournal()
    for i in range(5):
        j.append({"n": i, "decision": "NO_TRADE" if i % 2 else "BUY"})
    assert S.replay_hash(j.records) == j.head
    tampered = [dict(r) for r in j.records]
    tampered[2]["decision"] = "SELL"
    assert S.replay_hash(tampered) != j.head


# ---- FE-0073 ----

def test_network_flap_with_replays_and_disorder_yields_one_ordered_stream() -> None:
    d = S.PacketDeduper()
    for pid, seq in (("p1", 1), ("p3", 3), ("p2", 2)):
        assert d.offer(pid, seq, {"seq": seq})
    d.reconnect()
    for pid, seq in (("p2", 2), ("p3", 3), ("p4", 4)):   # replay after resubscribe
        d.offer(pid, seq, {"seq": seq})
    assert [p["seq"] for p in d.ordered()] == [1, 2, 3, 4]
    assert d.duplicates == 2 and d.reconnects == 1


# ---- FE-0074 ----

def test_one_alert_per_root_cause_and_entries_halt() -> None:
    a = S.AlertManager()
    assert a.raise_alert("STALE_QUOTES") is True
    assert a.raise_alert("STALE_QUOTES") is False
    assert a.raise_alert("LEDGER_DIFF") is True
    assert a.alerts == ["STALE_QUOTES", "LEDGER_DIFF"] and a.entries_halted is True


# ---- FE-0075 ----

def test_end_of_session_finds_forgotten_and_unapproved_inventory() -> None:
    assert S.end_of_session_check({"A": 10, "B": 0}, intraday_only=True, carry_allowed=set()) == ["FORGOTTEN_INVENTORY:A:10"]
    assert S.end_of_session_check({"A": 10}, intraday_only=False, carry_allowed={"A"}) == []
    assert S.end_of_session_check({"A": 10}, intraday_only=False, carry_allowed=set()) == ["UNAPPROVED_OVERNIGHT:A:10"]


# ---- FE-0076 ----

def test_missing_segment_or_incident_downgrades_the_session() -> None:
    full = set(S.SEGMENTS)
    assert S.completeness_report("s1", full, [])["eligibility"] == "ELIGIBLE"
    r = S.completeness_report("s2", full - {"fills"}, [])
    assert r["eligibility"] == "NOT_ELIGIBLE_DOWNGRADED" and r["missing_segments"] == ["fills"]
    assert S.completeness_report("s3", full, ["DUPLICATE_ORDER"])["eligibility"] == "NOT_ELIGIBLE_DOWNGRADED"


# ---- FE-0077 ----

def test_backup_restores_to_recorded_hashes_and_detects_corruption(tmp_path: Path) -> None:
    src = tmp_path / "live"
    src.mkdir()
    files = []
    for name, text in (("oms.json", "{}"), ("ledger.jsonl", "a\nb\n"), ("manifests.jsonl", "m\n")):
        (src / name).write_text(text, encoding="utf-8", newline="\n")
        files.append(src / name)
    bk = tmp_path / "bk"
    S.backup(files, bk)
    assert S.verify_restore(bk) == []
    (bk / "ledger.jsonl").write_text("corrupt", encoding="utf-8")
    assert S.verify_restore(bk) == ["mismatch ledger.jsonl"]


def test_backup_preserves_dependency_paths_and_restores_without_source_tree(tmp_path: Path) -> None:
    source = tmp_path / "live"
    (source / "journals").mkdir(parents=True)
    (source / "quotes").mkdir()
    (source / "journals" / "session.jsonl").write_text("journal\n", encoding="utf-8")
    (source / "quotes" / "session.jsonl").write_text("quotes\n", encoding="utf-8")
    bundle = tmp_path / "offline-bundle"

    result = S.backup([source / "journals" / "session.jsonl", source / "quotes" / "session.jsonl"], bundle)
    assert set(result) == {"journals/session.jsonl", "quotes/session.jsonl"}
    assert S.verify_restore(bundle) == []
    restored = tmp_path / "isolated-restore"
    shutil.copytree(bundle, restored)
    shutil.rmtree(source)
    assert S.verify_restore(restored) == []
    assert (restored / "journals" / "session.jsonl").read_text(encoding="utf-8") == "journal\n"


def test_backup_rejects_overwrite_and_manifest_path_traversal(tmp_path: Path) -> None:
    source = tmp_path / "live.jsonl"
    source.write_text("ok\n", encoding="utf-8")
    bundle = tmp_path / "bundle"
    S.backup([source], bundle)
    with pytest.raises(S.SessionError, match="already exists"):
        S.backup([source], bundle)
    manifest = json.loads((bundle / "BACKUP_MANIFEST.json").read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = "../escape.jsonl"
    (bundle / "BACKUP_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert any("unsafe" in problem for problem in S.verify_restore(bundle))


# ---- FE-0078 ----

def test_runbooks_cover_each_phase_and_unknown_phase_is_refused() -> None:
    assert "Run preflight" in S.render_runbook("morning")
    assert "automatically" in S.render_runbook("during")
    assert "completeness report" in S.render_runbook("evening")
    with pytest.raises(S.SessionError):
        S.render_runbook("midnight")
