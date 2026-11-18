import pytest

from financial_brain.forward_edge.custody import CustodyStatus
from financial_brain.forward_edge.evidence_labels import (
    EvidenceClass,
    EvidenceLabelError,
    classify,
    stamp_report,
)
from financial_brain.forward_edge.evidence_stats import ClaimTier, StatsError, label_rows


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("shadow", EvidenceClass.EXPLORATORY_SHADOW),
        ("replay", EvidenceClass.HISTORICAL_REPLAY),
        ("fixture", EvidenceClass.HISTORICAL_REPLAY),
        ("live", EvidenceClass.LOCAL_PAPER),
        ("paper", EvidenceClass.LOCAL_PAPER),
    ],
)
def test_modes_have_distinct_fail_closed_evidence_classes(mode, expected):
    assert classify(mode) is expected


def test_local_paper_requires_every_independent_evidence_fact_for_higher_label():
    facts = {
        "market_observed": True,
        "pre_entry_sealed": True,
        "session_complete": True,
        "custody_status": CustodyStatus.VERIFIED,
    }
    assert classify("local_paper", **facts) is EvidenceClass.INDEPENDENTLY_ATTESTED_FORWARD
    for missing in facts:
        assert classify("local_paper", **{**facts, missing: False if missing != "custody_status" else None}) \
            is EvidenceClass.LOCAL_PAPER


def test_replay_cannot_be_upgraded_by_attestation_fields():
    assert classify(
        "replay", market_observed=True, pre_entry_sealed=True, session_complete=True,
        custody_status=CustodyStatus.VERIFIED,
    ) is EvidenceClass.HISTORICAL_REPLAY


def test_report_stamping_rejects_caller_claimed_independent_forward_label():
    with pytest.raises(EvidenceLabelError, match="conflicts"):
        stamp_report({"evidence_class": "INDEPENDENTLY_ATTESTED_FORWARD"}, "fixture_replay")
    with pytest.raises(EvidenceLabelError, match="without verified"):
        stamp_report({"independently_attested": True}, "local_paper")
    with pytest.raises(EvidenceLabelError, match="forward claim"):
        stamp_report({"tier": "PAPER_FORWARD"}, "replay")


def test_unknown_mode_fails_closed():
    with pytest.raises(EvidenceLabelError, match="unsupported"):
        classify("looks_forward_to_me")


def test_export_rows_cannot_label_replay_or_local_paper_as_independent_forward():
    with pytest.raises(StatsError, match="conflicts with evidence mode"):
        label_rows([{"tier": ClaimTier.INDEPENDENT_FORWARD, "mode": "replay"}])
    with pytest.raises(StatsError, match="conflicts with evidence mode"):
        label_rows([{"tier": ClaimTier.INDEPENDENT_FORWARD, "mode": "live"}])
    historical = label_rows([{"tier": ClaimTier.HISTORICAL, "mode": "fixture_replay"}])
    assert historical[0]["evidence_class"] == "HISTORICAL_REPLAY"
    assert historical[0]["independently_attested"] is False


def test_export_independent_forward_requires_each_source_fact():
    row = {
        "tier": ClaimTier.INDEPENDENT_FORWARD,
        "mode": "local_paper",
        "market_observed": True,
        "pre_entry_sealed": True,
        "session_complete": True,
        "custody_status": CustodyStatus.VERIFIED,
    }
    labelled = label_rows([row])
    assert labelled[0]["evidence_class"] == "INDEPENDENTLY_ATTESTED_FORWARD"
    assert labelled[0]["independently_attested"] is True
    with pytest.raises(StatsError, match="conflicts with evidence mode"):
        label_rows([{**row, "pre_entry_sealed": False}])


def test_generic_exporter_cannot_assert_deployment_validation():
    with pytest.raises(StatsError, match="deployment validation"):
        label_rows([{"tier": ClaimTier.DEPLOYMENT_VALIDATED, "mode": "live"}])
