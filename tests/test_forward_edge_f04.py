"""F04 - forward decision custody (FE-0045 .. FE-0054). Offline. Without a configured provider every session is LOCAL_ONLY."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from financial_brain.forward_edge import decision_custody as D
from financial_brain.forward_edge.custody import CustodyStatus, Receipt

H = "d" * 64
CHARTER = "c" * 64
DECIDE = datetime(2027, 1, 4, 3, 46, tzinfo=timezone.utc)   # 09:16 IST
ENTRY = datetime(2027, 1, 4, 4, 0, tzinfo=timezone.utc)     # 09:30 IST


def _cohorts(*spans):
    return tuple(D.Cohort(f"C{i + 1}", s, e) for i, (s, e) in enumerate(spans))


def _manifest(session="2027-01-04", **over) -> D.DecisionManifest:
    base = dict(session_id=session, strategy_version_sha256=H, snapshot_sha256=H,
                decision_at_utc=DECIDE.isoformat(), entry_earliest_at_utc=ENTRY.isoformat(), side="BUY",
                instrument_id="INE009A01021", intended_quantity=10, risk_policy_sha256=H,
                benchmark_id="NIFTY_CASH", evidence_tier="LOCAL_PAPER")
    base.update(over)
    return D.DecisionManifest(**base)


# ---- FE-0045 frozen cohort schedule ----

def test_schedule_rejects_overlapping_and_unsorted_cohorts() -> None:
    with pytest.raises(D.CustodyError, match="overlap"):
        D.CohortSchedule(CHARTER, _cohorts(("2027-01-04", "2027-02-01"), ("2027-02-01", "2027-03-01")))
    with pytest.raises(D.CustodyError, match="sorted"):
        D.CohortSchedule(CHARTER, (D.Cohort("B", "2027-03-01", "2027-03-31"), D.Cohort("A", "2027-01-04", "2027-02-01")))


def test_schedule_hash_is_pinned_once_observations_begin() -> None:
    s = D.CohortSchedule(CHARTER, _cohorts(("2027-01-04", "2027-02-01")))
    D.assert_schedule_unchanged(s, s.sha256(), observations_started=True)
    changed = D.CohortSchedule(CHARTER, _cohorts(("2027-01-04", "2027-02-02")))
    with pytest.raises(D.CustodyError, match="new prospective clock"):
        D.assert_schedule_unchanged(changed, s.sha256(), observations_started=True)


# ---- FE-0046 canonical manifest, golden hash ----

def test_manifest_canonical_bytes_are_stable_and_hash_matches_golden() -> None:
    m = _manifest()
    assert m.canonical() == _manifest().canonical()
    # Pinned canonical ASCII JSON digest is checked on both CI operating systems.
    assert m.sha256() == "49a11cedc6c6d357e9007bab78870f8462ddc56aab9362504428dea8921d9337"
    assert _manifest(intended_quantity=11).sha256() != m.sha256()


def test_manifest_rejects_floats_bad_hashes_and_late_decisions() -> None:
    with pytest.raises(D.CustodyError, match="quantity"):
        _manifest(intended_quantity=10.5)
    with pytest.raises(D.CustodyError, match="sha256"):
        _manifest(snapshot_sha256="x")
    with pytest.raises(D.CustodyError, match="precede"):
        _manifest(decision_at_utc=ENTRY.isoformat())
    with pytest.raises(D.CustodyError, match="side"):
        _manifest(side="SHORT")


# ---- FE-0048 record before entry; FE-0049 denominators ----

def test_manifest_must_be_recorded_before_entry_and_never_overwritten() -> None:
    j = D.ManifestJournal()
    m = _manifest()
    with pytest.raises(D.CustodyError, match="at or after"):
        j.record(m, recorded_at_utc=ENTRY)
    with pytest.raises(D.CustodyError, match="before its decision"):
        j.record(m, recorded_at_utc=DECIDE - timedelta(minutes=1))
    j.record(m, recorded_at_utc=DECIDE + timedelta(seconds=5))
    with pytest.raises(D.CustodyError, match="overwrite"):
        j.record(_manifest(intended_quantity=99), recorded_at_utc=DECIDE + timedelta(seconds=6))


def test_durable_manifest_journal_replays_decisions_and_all_terminal_outcome_classes(tmp_path: Path) -> None:
    path = tmp_path / "decision-custody.jsonl"
    journal = D.ManifestJournal(path)
    manifest = _manifest()
    journal.record(manifest, recorded_at_utc=DECIDE + timedelta(seconds=5))
    outcomes = {
        "2027-01-04": "SUBMITTED",
        "2027-01-05": "NO_SIGNAL",
        "2027-01-06": "NO_TRADE",
        "2027-01-07": "REJECTED",
        "2027-01-08": "UNKNOWN_ORDER",
        "2027-01-09": "UNSUCCESSFUL",
    }
    for session_id, outcome in outcomes.items():
        journal.outcome(session_id, outcome)

    recovered = D.ManifestJournal(path)
    assert recovered.manifests() == [manifest]
    assert recovered._recorded_at[manifest.session_id] == DECIDE + timedelta(seconds=5)
    counts = recovered.denominator(list(outcomes))
    assert counts["SCHEDULED"] == 6
    assert counts["UNRECORDED"] == 0
    assert all(counts[name] == 1 for name in outcomes.values())


def test_status_is_local_only_without_a_receipt_and_verified_only_with_a_pinned_one() -> None:
    j = D.ManifestJournal()
    m = _manifest()
    j.record(m, recorded_at_utc=DECIDE + timedelta(seconds=5))
    assert j.status("2027-01-04", {}) is CustodyStatus.LOCAL_ONLY
    assert j.verified_session_ids({}) == set()


def test_zero_signal_and_unsuccessful_sessions_stay_in_the_denominator() -> None:
    j = D.ManifestJournal()
    j.record(_manifest("2027-01-04"), recorded_at_utc=DECIDE + timedelta(seconds=5))
    j.outcome("2027-01-04", "SUBMITTED")
    j.outcome("2027-01-05", "NO_SIGNAL")
    j.outcome("2027-01-06", "UNSUCCESSFUL")
    den = j.denominator(["2027-01-04", "2027-01-05", "2027-01-06", "2027-01-07"])
    assert den["SCHEDULED"] == 4 and den["UNRECORDED"] == 1
    assert den["NO_SIGNAL"] == 1 and den["UNSUCCESSFUL"] == 1 and den["SUBMITTED"] == 1
    with pytest.raises(D.CustodyError, match="already"):
        j.outcome("2027-01-04", "WITHHELD")


def test_receipt_cannot_be_attached_twice_or_to_unknown_sessions() -> None:
    j = D.ManifestJournal()
    j.record(_manifest(), recorded_at_utc=DECIDE + timedelta(seconds=5))
    r = Receipt("tsa", "k1", H, DECIDE + timedelta(seconds=6), "AAAA")
    with pytest.raises(D.CustodyError, match="no manifest"):
        j.attach_receipt("missing", r)
    j.attach_receipt("2027-01-04", r)
    with pytest.raises(D.CustodyError, match="already"):
        j.attach_receipt("2027-01-04", r)


# ---- FE-0054 attestation bundle ----

def test_bundle_verifies_and_detects_tampering(tmp_path: Path) -> None:
    j = D.ManifestJournal()
    j.record(_manifest(), recorded_at_utc=DECIDE + timedelta(seconds=5))
    j.outcome("2027-01-04", "SUBMITTED")
    sched = D.CohortSchedule(CHARTER, _cohorts(("2027-01-04", "2027-02-01")))
    out = tmp_path / "bundle"
    D.export_bundle(j, sched, out, sessions=["2027-01-04"])
    assert D.verify_bundle(out) == []
    original = (out / "manifests.jsonl").read_text(encoding="utf-8")
    text = original.replace('"intended_quantity": 10', '"intended_quantity": 11')
    assert text != original  # the edit must actually happen
    (out / "manifests.jsonl").write_text(text, encoding="utf-8")
    problems = D.verify_bundle(out)
    assert any("manifests.jsonl" in p for p in problems)


def test_bundle_copy_verifies_on_another_location(tmp_path: Path) -> None:
    j = D.ManifestJournal()
    j.record(_manifest(), recorded_at_utc=DECIDE + timedelta(seconds=5))
    sched = D.CohortSchedule(CHARTER, _cohorts(("2027-01-04", "2027-02-01")))
    src = tmp_path / "a"
    D.export_bundle(j, sched, src, sessions=["2027-01-04"])
    dst = tmp_path / "b"
    dst.mkdir()
    for p in src.iterdir():
        (dst / p.name).write_bytes(p.read_bytes())
    assert D.verify_bundle(dst) == []
    assert json.loads((dst / "schedule.json").read_text(encoding="utf-8"))["charter_sha256"] == CHARTER
