from __future__ import annotations

from scripts.trial_preservation_audit import build_report, verify_baseline_blobs


def test_audit_confirms_pins_and_does_not_invent_missing_trial_provenance() -> None:
    report = build_report()
    assert report["ledger"]["pin_verification_errors"] == []
    assert report["ledger"]["trial_count"] == 13
    assert report["coverage"]["registration_timestamps"] == 13
    assert report["coverage"]["failure_outcomes"] == 13
    assert report["coverage"]["input_hashes"] < 13
    assert report["coverage"]["historical_read_windows"] < 13
    assert any("input_hashes" in row["missing_historical_evidence"] for row in report["trials"])


def test_preservation_audit_is_explicit_about_byte_integrity_vs_content_coverage() -> None:
    report = build_report()
    assert "proves preservation of bytes, not that" in report["conclusion"]
    assert "never reconstructed or backfilled" in report["scope"]


def test_all_protected_sources_match_original_main_blobs() -> None:
    report = build_report()
    assert verify_baseline_blobs() == []
    assert report["main_baseline"]["protected_source_count"] == 9
    assert report["main_baseline"]["blob_mismatches"] == []
