from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.approvals import Approval
from financial_brain.forward_edge.exclusions import (
    ExclusionError,
    ExclusionRegistry,
    ExclusionRequest,
    sensitivity_report,
)

NOW = datetime(2026, 11, 13, 9, tzinfo=timezone.utc)


def request(**changes):
    values = {
        "session_id": "session-2026-11-10",
        "session_date": "2026-11-10",
        "reason_code": "SOURCE_CORRUPTION",
        "evidence_sha256": "a" * 64,
        "rationale": "Archive checksum differs from the source receipt.",
        "reported_by": "operator:one",
    }
    return ExclusionRequest(**{**values, **changes})


def approval(req, *, reviewer="reviewer:two", scope=None, kind="DATA_EXCLUSION_REVIEW"):
    return Approval(
        kind=kind,
        subject=req.session_id,
        scope_sha256=scope or req.sha256(),
        actor_id=reviewer,
        actor_type="human",
        approved_at_utc=NOW - timedelta(minutes=1),
        expires_at_utc=NOW + timedelta(days=1),
    )


def test_exclusion_requires_a_distinct_scoped_human_reviewer_and_recovers(tmp_path):
    registry_path = tmp_path / "exclusions.jsonl"
    registry = ExclusionRegistry(registry_path)
    item = request()
    digest = registry.record(item, approval(item), recorded_at=NOW)
    assert digest == item.sha256()
    view = registry.entries()
    view[0]["request"]["session_date"] = "2026-11-11"
    assert registry.entries()[0]["request"]["session_date"] == item.session_date
    recovered = ExclusionRegistry(registry_path)
    assert recovered.entries() == registry.entries()
    with pytest.raises(ExclusionError, match="immutable"):
        recovered.record(request(rationale="different reason"), approval(request(rationale="different reason")),
                         recorded_at=NOW)


@pytest.mark.parametrize(
    "item, review, message",
    [
        (request(reason_code="LOSING_DAY"), None, "limited to factual"),
        (request(), "same", "different human"),
        (request(), "agent", "cannot approve"),
        (request(), "stale", "stale approval"),
    ],
)
def test_discretionary_exclusion_or_nonindependent_review_is_rejected(tmp_path, item, review, message):
    registry = ExclusionRegistry(tmp_path / "exclusions.jsonl")
    if item.reason_code == "LOSING_DAY":
        with pytest.raises(ExclusionError, match=message):
            registry.record(item, approval(item), recorded_at=NOW)
        return
    if review == "same":
        reviewer_approval = approval(item, reviewer=item.reported_by)
    elif review == "agent":
        reviewer_approval = approval(item, reviewer="agent:reviewer")
    elif review == "stale":
        reviewer_approval = approval(item, scope="b" * 64)
    else:
        raise AssertionError(review)
    with pytest.raises(ExclusionError, match=message):
        registry.record(item, reviewer_approval, recorded_at=NOW)
    assert registry.entries() == []


def test_excluded_date_stays_in_fixed_denominator_and_adverse_sensitivity(tmp_path):
    registry = ExclusionRegistry(tmp_path / "exclusions.jsonl")
    item = request()
    registry.record(item, approval(item), recorded_at=NOW)
    report = sensitivity_report(
        scheduled_sessions=["2026-11-10", "2026-11-11", "2026-11-12"],
        daily_net_returns={"2026-11-11": 0.01, "2026-11-12": 0.03},
        exclusions=registry,
    )
    assert report["status"] == "REVIEW_REQUIRED"
    assert report["scheduled_denominator"] == 3
    assert report["approved_exclusions"] == 1
    assert report["excluded_dates"] == ["2026-11-10"]
    assert report["fixed_denominator_zero_fill_sensitivity"] == pytest.approx(0.04 / 3)
    assert report["fixed_denominator_adverse_sensitivity"] == pytest.approx((0.04 - 1.0) / 3)
    assert report["promotion_eligible"] is False


def test_unreviewed_missing_dates_remain_in_denominator_and_get_adverse_sensitivity(tmp_path):
    report = sensitivity_report(
        scheduled_sessions=["2026-11-10", "2026-11-11", "2026-11-12"],
        daily_net_returns={"2026-11-10": 0.03},
        exclusions=ExclusionRegistry(tmp_path / "empty.jsonl"),
    )
    assert report["scheduled_denominator"] == 3
    assert report["unreviewed_missing_dates"] == ["2026-11-11", "2026-11-12"]
    assert report["fixed_denominator_adverse_sensitivity"] == pytest.approx((0.03 - 2.0) / 3)


def test_observed_and_excluded_same_date_is_rejected(tmp_path):
    registry = ExclusionRegistry(tmp_path / "exclusions.jsonl")
    item = request()
    registry.record(item, approval(item), recorded_at=NOW)
    with pytest.raises(ExclusionError, match="both observed and excluded"):
        sensitivity_report(scheduled_sessions=["2026-11-10"], daily_net_returns={"2026-11-10": 0.01},
                           exclusions=registry)
