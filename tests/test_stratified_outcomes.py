import pytest

from financial_brain.forward_edge.stratified_outcomes import (
    DIMENSIONS,
    StratificationError,
    stratify_outcomes,
)


SESSIONS = ["2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05"]
REGISTRY = {dimension: ["base"] for dimension in DIMENSIONS}


def _rows(labels=None):
    labels = labels or {dimension: "base" for dimension in DIMENSIONS}
    return [
        {"dimension": dimension, "stratum": labels[dimension], "session": day,
         "net_alpha": alpha, "after_cost_utility": utility}
        for dimension in DIMENSIONS
        for day, alpha, utility in zip(SESSIONS, [0.010, 0.011, 0.009, 0.0105],
                                       [0.004, 0.0045, 0.0035, 0.0042], strict=True)
    ]


def test_all_registered_dimensions_report_simultaneous_net_uncertainty_bands():
    report = stratify_outcomes(
        scheduled_sessions=SESSIONS, registered_strata=REGISTRY,
        observations=_rows(), hac_lags=1,
    )
    assert report["status"] == "STRATIFIED"
    assert report["registered_strata_count"] == 5
    assert report["simultaneous_metric_count"] == 10
    assert report["positive_strata_are_not_a_global_profitability_claim"] is True
    assert {row["dimension"] for row in report["strata"]} == set(DIMENSIONS)
    assert all(row["daily_net_alpha"]["simultaneous_lower_bound"] > 0
               for row in report["strata"])


def test_registered_empty_regime_is_visible_and_keeps_full_denominator():
    registry = {**REGISTRY, "market_regime": ["bear", "bull", "sideways"]}
    rows = _rows()
    for row in rows:
        if row["dimension"] == "market_regime":
            row["stratum"] = "bull"
    report = stratify_outcomes(
        scheduled_sessions=SESSIONS, registered_strata=registry,
        observations=rows, hac_lags=1,
    )
    bear = next(x for x in report["strata"]
                if x["dimension"] == "market_regime" and x["stratum"] == "bear")
    assert report["status"] == "FRAGMENTED_OR_INCOMPLETE"
    assert bear["scheduled_denominator"] == 4
    assert bear["observed_sessions"] == 0
    assert bear["missing_sessions"] == SESSIONS
    assert bear["daily_net_alpha"]["status"] == "INCOMPLETE"


def test_missing_session_is_not_dropped_and_withholds_group_bounds():
    rows = [row for row in _rows() if not (
        row["dimension"] == "liquidity" and row["session"] == SESSIONS[-1]
    )]
    report = stratify_outcomes(
        scheduled_sessions=SESSIONS, registered_strata=REGISTRY,
        observations=rows, hac_lags=1,
    )
    liquidity = next(x for x in report["strata"] if x["dimension"] == "liquidity")
    assert liquidity["scheduled_denominator"] == 4
    assert liquidity["daily_net_alpha"]["simultaneous_lower_bound"] is None
    assert liquidity["daily_net_alpha"]["status"] == "INCOMPLETE"


def test_more_registered_strata_widen_the_simultaneous_interval():
    one = stratify_outcomes(scheduled_sessions=SESSIONS, registered_strata=REGISTRY,
                            observations=_rows(), hac_lags=1)
    many_registry = {**REGISTRY, "volatility": ["base", "high", "low"]}
    many_rows = _rows()
    many = stratify_outcomes(scheduled_sessions=SESSIONS, registered_strata=many_registry,
                             observations=many_rows, hac_lags=1)
    lower_one = next(x for x in one["strata"] if x["dimension"] == "volatility")["daily_net_alpha"]["simultaneous_lower_bound"]
    lower_many = next(x for x in many["strata"] if x["dimension"] == "volatility")["daily_net_alpha"]["simultaneous_lower_bound"]
    assert lower_many < lower_one


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda rows: rows.append(dict(rows[0])), "duplicate outcome"),
        (lambda rows: rows.__setitem__(0, {**rows[0], "stratum": "unregistered"}),
         "unregistered"),
        (lambda rows: rows.__setitem__(0, {**rows[0], "session": "2026-01-06"}),
         "unscheduled"),
        (lambda rows: rows.__setitem__(0, {**rows[0], "net_alpha": float("nan")}),
         "finite"),
    ],
)
def test_bad_stratum_rows_are_rejected(mutate, message):
    rows = _rows()
    mutate(rows)
    with pytest.raises(StratificationError, match=message):
        stratify_outcomes(scheduled_sessions=SESSIONS, registered_strata=REGISTRY,
                          observations=rows, hac_lags=1)


def test_registry_requires_all_dimensions_and_hac_must_fit_schedule():
    with pytest.raises(StratificationError, match="all five"):
        stratify_outcomes(scheduled_sessions=SESSIONS, registered_strata={"volatility": ["low"]},
                          observations=[], hac_lags=1)
    with pytest.raises(StratificationError, match="smaller"):
        stratify_outcomes(scheduled_sessions=SESSIONS, registered_strata=REGISTRY,
                          observations=[], hac_lags=4)
