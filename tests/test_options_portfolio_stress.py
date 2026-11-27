from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.options_portfolio_stress import (
    OptionRiskPosition,
    OptionsPortfolioStressError,
    PortfolioShock,
    stress_option_portfolio,
)


NOW = datetime(2026, 11, 27, 10, tzinfo=timezone.utc)
SETTLEMENT = NOW + timedelta(days=7)


def position(pid, strategy, underlying="NIFTY", **overrides):
    values = dict(
        position_id=pid,
        strategy_id=strategy,
        underlying=underlying,
        settlement_at=SETTLEMENT,
        option_type="CE",
        signed_contracts=1,
        lot_size=25,
        strike=25_000,
        spot=25_000,
        mark_per_unit=200,
        delta=0.5,
        gamma=0.0001,
        vega_per_vol_point=4,
        theta_per_day=-2,
        iv=20,
        fees_per_contract_inr=30,
        quote_at=NOW,
        quote_source_sha256="a" * 64,
    )
    values.update(overrides)
    return OptionRiskPosition(**values)


def shock(shock_id, horizon, **returns):
    return PortfolioShock(
        shock_id,
        horizon,
        tuple(sorted(returns.items())),
        tuple((underlying, 0.0) for underlying in sorted(returns)),
    )


def test_aggregates_same_underlying_across_strategies_and_applies_shared_shock():
    positions = (
        position("long-a", "swing", signed_contracts=2),
        position("short-b", "intraday", signed_contracts=-1),
    )
    result = stress_option_portfolio(
        positions,
        (shock("gap-down", NOW + timedelta(hours=1), NIFTY=-0.05),),
        as_of=NOW,
    )

    assert result["net_delta_inr_by_underlying"]["NIFTY"] == pytest.approx(312_500)
    assert result["gross_delta_inr_by_underlying"]["NIFTY"] == pytest.approx(937_500)
    assert result["position_count"] == 2
    assert result["worst_scenario_id"] == "gap-down"
    assert result["quote_source_sha256_by_position"] == {
        "long-a": "a" * 64,
        "short-b": "a" * 64,
    }


def test_correlated_underlying_shocks_and_expiry_settlement_use_intrinsic_value():
    expiry = NOW + timedelta(hours=2)
    positions = (
        position("nifty-call", "defined-risk", settlement_at=expiry),
        position(
            "banknifty-put",
            "hedge",
            underlying="BANKNIFTY",
            option_type="PE",
            strike=50_000,
            spot=50_000,
            settlement_at=expiry,
        ),
    )
    result = stress_option_portfolio(
        positions,
        (shock("correlated-gap", NOW + timedelta(hours=3), NIFTY=-0.10, BANKNIFTY=-0.12),),
        as_of=NOW,
    )

    assert result["worst_scenario_id"] == "correlated-gap"
    outcomes = result["scenario_results"][0]
    assert outcomes["gross_pnl_inr"] == pytest.approx(25 * (0 - 200) + 25 * (6_000 - 200))
    assert outcomes["fees_inr"] == 60
    assert result["settlement_scenarios_included"] is True


@pytest.mark.parametrize(
    "bad_position",
    [
        {"quote_at": NOW - timedelta(minutes=1)},
        {"quote_source_sha256": "not-a-source-hash"},
        {"settlement_at": datetime(2026, 12, 4)},
    ],
)
def test_missing_stale_or_unattested_position_evidence_fails_closed(bad_position):
    with pytest.raises(OptionsPortfolioStressError):
        stress_option_portfolio(
            (position("p", "s", **bad_position),),
            (shock("shock", NOW + timedelta(hours=1), NIFTY=-0.01),),
            as_of=NOW,
        )


def test_duplicate_shock_entries_and_negative_stressed_iv_are_rejected():
    duplicate = PortfolioShock(
        "duplicate",
        NOW + timedelta(hours=1),
        (("NIFTY", -0.01), ("NIFTY", -0.02)),
        (("NIFTY", 0.0),),
    )
    with pytest.raises(OptionsPortfolioStressError, match="duplicate"):
        stress_option_portfolio((position("p", "s"),), (duplicate,), as_of=NOW)

    negative_iv = PortfolioShock(
        "negative-iv",
        NOW + timedelta(hours=1),
        (("NIFTY", -0.01),),
        (("NIFTY", -21.0),),
    )
    with pytest.raises(OptionsPortfolioStressError, match="negative implied volatility"):
        stress_option_portfolio((position("p", "s"),), (negative_iv,), as_of=NOW)

