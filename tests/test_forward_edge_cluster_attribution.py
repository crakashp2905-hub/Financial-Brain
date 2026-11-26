from datetime import date, timedelta
import math

import pytest

from financial_brain.forward_edge.cluster_attribution import (
    AttributionObservation,
    ClusterAttributionError,
    TradeSessionObservation,
    two_way_cluster_mean,
    two_way_trade_session_mean,
)


def _panel():
    start = date(2026, 1, 1)
    rows = []
    for d in range(12):
        for instrument in range(8):
            value = (0.01 * ((d % 4) - 1.5) + 0.02 * ((instrument % 3) - 1)
                     + 0.003 * ((d * instrument) % 5))
            rows.append(AttributionObservation(start + timedelta(days=d), f"I{instrument}", value))
    return tuple(rows)


def test_two_way_cluster_report_accounts_for_both_date_and_instrument_dependence() -> None:
    rows = _panel()
    report = two_way_cluster_mean(rows)
    iid_se = (sum((row.net_attribution - report.mean_attribution) ** 2 for row in rows)
              / (len(rows) - 1) / len(rows)) ** 0.5
    assert report.observations == 96
    assert report.date_clusters == 12
    assert report.instrument_clusters == 8
    assert report.ci_lower <= report.mean_attribution <= report.ci_upper
    assert report.standard_error >= iid_se
    assert report.method.startswith("TWO_WAY_CLUSTER")
    assert report.report_hash


def test_cluster_attribution_rejects_duplicate_cells_and_too_few_clusters() -> None:
    one = AttributionObservation(date(2026, 1, 1), "A", 0.01)
    with pytest.raises(ClusterAttributionError, match="four"):
        two_way_cluster_mean((one, one))
    duplicate = (one, AttributionObservation(date(2026, 1, 1), "A", 0.02),
                 AttributionObservation(date(2026, 1, 2), "B", 0.0),
                 AttributionObservation(date(2026, 1, 2), "C", 0.0))
    with pytest.raises(ClusterAttributionError, match="one attribution"):
        two_way_cluster_mean(duplicate)
    only_one_date = tuple(AttributionObservation(date(2026, 1, 1), f"I{i}", float(i)) for i in range(4))
    with pytest.raises(ClusterAttributionError, match="two date"):
        two_way_cluster_mean(only_one_date)


def test_cluster_values_must_be_finite() -> None:
    with pytest.raises(ClusterAttributionError, match="finite"):
        AttributionObservation(date(2026, 1, 1), "A", math.nan)


def test_trade_session_cluster_reports_effective_clusters_not_raw_marks() -> None:
    start = date(2026, 2, 1)
    rows = tuple(TradeSessionObservation(start + timedelta(days=session), f"T{trade}",
                                         0.001 * (session - trade))
                 for session in range(6) for trade in range(4))
    report = two_way_trade_session_mean(rows)
    assert report.observations == 24
    assert report.session_clusters == 6
    assert report.trade_clusters == 4
    assert report.effective_independent_observations == pytest.approx(4.0)
    assert report.effective_independent_observations < report.observations
    assert report.ci_lower <= report.mean_attribution <= report.ci_upper
    assert report.report_hash


def test_trade_session_cluster_rejects_duplicate_cells_and_one_cluster_dimension() -> None:
    one = TradeSessionObservation(date(2026, 1, 1), "T1", 0.01)
    with pytest.raises(ClusterAttributionError, match="four"):
        two_way_trade_session_mean((one, one))
    rows = tuple(TradeSessionObservation(date(2026, 1, 1) + timedelta(days=i), "T1", i * 0.01)
                 for i in range(4))
    with pytest.raises(ClusterAttributionError, match="two session and two trade"):
        two_way_trade_session_mean(rows)
