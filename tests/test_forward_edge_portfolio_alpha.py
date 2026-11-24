from datetime import date

import pytest

from financial_brain.forward_edge.portfolio_alpha import (
    PortfolioAlphaError,
    PortfolioDay,
    compute_portfolio_alpha,
)


def test_after_cost_net_alpha_includes_cash_days_and_carried_exposure() -> None:
    d1, d2, d3 = date(2026, 11, 16), date(2026, 11, 17), date(2026, 11, 18)
    days = (
        PortfolioDay(d1, 100_000, {"NSE:ABC": 1_000}, {"brokerage": 300, "taxes": 200},
                     100, 0, 0.005, 50_000, 40_000),
        PortfolioDay(d2, 100_400, {}, {}, 0, 10, -0.002, 0, 0),
        PortfolioDay(d3, 100_410, {"NSE:ABC": -300}, {"fees": 100}, 20, 0, 0.001,
                     25_000, -25_000),
    )
    report = compute_portfolio_alpha(days, (d1, d2, d3))
    assert report.sessions == 3
    assert report.cash_only_sessions == 1
    assert report.days[0]["net_pnl"] == 400
    assert report.days[1]["net_pnl"] == 10
    assert report.days[2]["net_pnl"] == -420
    assert report.days[0]["net_alpha"] < 0  # costs can consume a gross winner
    assert report.days[2]["gross_exposure"] == 25_000
    assert report.report_hash


def test_missing_exchange_session_and_invalid_costs_fail_closed() -> None:
    first, second = date(2026, 11, 16), date(2026, 11, 17)
    one = PortfolioDay(first, 1000, {}, {}, 0, 0, 0, 0, 0)
    with pytest.raises(PortfolioAlphaError, match="missing"):
        compute_portfolio_alpha((one,), (first, second))
    with pytest.raises(PortfolioAlphaError, match="cannot be negative"):
        PortfolioDay(second, 1000, {}, {"brokerage": -1}, 0, 0, 0, 0, 0)


def test_leveraged_return_uses_equity_denominator_and_exposure_is_checked() -> None:
    day = PortfolioDay(date(2026, 11, 16), 1000, {"future": 50}, {}, 5, 0, 0.01,
                       5000, 5000)
    report = compute_portfolio_alpha((day,), (day.session,))
    assert report.days[0]["portfolio_return"] == pytest.approx(0.045)
    with pytest.raises(PortfolioAlphaError, match="net exposure"):
        PortfolioDay(day.session, 1000, {}, {}, 0, 0, 0, 10, 11)
