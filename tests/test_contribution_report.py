import pytest

from financial_brain.forward_edge.contribution_report import (
    ContributionReportError,
    contribution_report,
)


SESSIONS = ["2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05"]
RETURNS = {day: value for day, value in zip(SESSIONS, [0.01, 0.02, -0.005, 0.015], strict=True)}
PROTOCOL = "a" * 64


def _contributions():
    rows = []
    values = [(0.007, 0.003), (0.015, 0.005), (-0.002, -0.003), (0.005, 0.01)]
    for day, (abc, xyz) in zip(SESSIONS, values, strict=True):
        rows.extend([
            {"contribution_id": f"{day}-abc", "session": day,
             "instrument": "ABC", "net_return_contribution": abc},
            {"contribution_id": f"{day}-xyz", "session": day,
             "instrument": "XYZ", "net_return_contribution": xyz},
        ])
    return rows


def _report(**changes):
    args = dict(
        scheduled_sessions=SESSIONS, daily_net_returns=RETURNS,
        contributions=_contributions(), protocol_sha256=PROTOCOL,
        frozen_at_utc="2026-01-01T00:00:00Z",
        first_observation_at_utc="2026-01-02T00:00:00Z",
    )
    args.update(changes)
    return contribution_report(**args)


def test_report_exposes_distribution_best_contributors_and_fixed_denominator_removals():
    report = _report()
    assert report["status"] == "COMPLETE"
    assert report["scheduled_denominator"] == 4
    assert report["observed_return_distribution"]["mean"] == pytest.approx(0.01)
    assert report["observed_return_distribution"]["minimum"] == -0.005
    assert report["best_day"] == {"key": "2026-01-03", "net_return_contribution": 0.02}
    assert report["best_instrument"]["key"] == "ABC"
    assert report["best_instrument"]["net_return_contribution"] == pytest.approx(0.025)
    assert report["best_month"]["key"] == "2026-01"
    removal = report["top_contributor_removal_sensitivity"]["best_instrument"]
    assert removal["remaining_sum_over_scheduled_sessions"] == pytest.approx(0.015)
    assert removal["remaining_mean_over_scheduled_sessions"] == pytest.approx(0.00375)
    assert report["one_day_contribution_share_of_positive_pnl"] == pytest.approx(0.02 / 0.045)


def test_report_hash_is_order_independent_and_bound_to_the_frozen_protocol():
    first = _report()
    second = _report(contributions=list(reversed(_contributions())))
    assert first["report_sha256"] == second["report_sha256"]
    assert first["protocol_sha256"] == PROTOCOL
    assert first["report_sha256"] != _report(protocol_sha256="b" * 64)["report_sha256"]


def test_missing_sessions_remain_in_denominator_and_report_is_incomplete():
    daily = {**RETURNS, "2026-01-05": None}
    rows = [row for row in _contributions() if row["session"] != "2026-01-05"]
    report = _report(daily_net_returns=daily, contributions=rows)
    assert report["status"] == "INCOMPLETE"
    assert report["scheduled_denominator"] == 4
    assert report["observed_sessions"] == 3
    assert report["missing_sessions"] == ["2026-01-05"]
    assert report["distribution_scope"].startswith("observed sessions only")


def test_contribution_reconciliation_is_enforced_and_zero_cash_days_can_have_no_rows():
    rows = _contributions()
    rows[0] = {**rows[0], "net_return_contribution": 0.5}
    with pytest.raises(ContributionReportError, match="do not reconcile"):
        _report(contributions=rows)
    daily = {**RETURNS, "2026-01-05": 0.0}
    rows = [row for row in _contributions() if row["session"] != "2026-01-05"]
    assert _report(daily_net_returns=daily, contributions=rows)["status"] == "COMPLETE"


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"frozen_at_utc": "2026-01-02T00:00:00Z"}, "before first observation"),
        ({"protocol_sha256": "bad"}, "SHA-256"),
        ({"scheduled_sessions": ["2026-01-03", "2026-01-02"]}, "unique and ordered"),
    ],
)
def test_protocol_and_schedule_are_pinned_before_observations(changes, message):
    with pytest.raises(ContributionReportError, match=message):
        _report(**changes)


def test_duplicate_or_nonfinite_contribution_inputs_are_rejected():
    rows = _contributions()
    rows.append(dict(rows[0]))
    with pytest.raises(ContributionReportError, match="unique"):
        _report(contributions=rows)
    rows = _contributions()
    rows[0] = {**rows[0], "net_return_contribution": float("nan")}
    with pytest.raises(ContributionReportError, match="finite"):
        _report(contributions=rows)
