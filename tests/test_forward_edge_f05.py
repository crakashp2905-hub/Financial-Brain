"""F05 - conservative paper execution (FE-0055 .. FE-0068). Offline. Cost rates below are FIXTURE values, not real rates."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from financial_brain.forward_edge import execution as X

T0 = datetime(2027, 1, 4, 3, 46, tzinfo=timezone.utc)
LAT = timedelta(milliseconds=200)


def _q(offset_ms: int, bid: str, ask: str, bq: int = 100, aq: int = 100) -> X.Quote:
    return X.Quote(T0 + timedelta(milliseconds=offset_ms), D(bid), D(ask), bq, aq)


def _intent(side="BUY", qty=50, limit=None, tick="0.05") -> X.OrderIntent:
    return X.OrderIntent("INFY", side, qty, T0, D(tick), D(limit) if limit else None)


# ---- FE-0055 canonical intent ----

def test_same_signal_from_two_engines_gives_the_same_intent() -> None:
    sig = {"instrument": "INFY", "side": "BUY", "quantity": 10, "decision_utc": T0, "limit": "1500.10"}
    a = X.intent_from_signal({**sig, "engine": "paper_engine"}, tick=D("0.05"))
    b = X.intent_from_signal({**sig, "engine": "kernel"}, tick=D("0.05"))
    assert a == b and a.limit == D("1500.10")


# ---- FE-0056 no hindsight ----

def test_fill_never_uses_a_quote_before_decision_plus_latency() -> None:
    quotes = [_q(-1000, "1400.00", "1400.05"), _q(100, "1450.00", "1450.05"), _q(300, "1500.00", "1500.05")]
    r = X.marketable_limit(_intent(), quotes, LAT)
    assert r.status == "FILLED" and r.avg_price == D("1500.05")   # the 300 ms quote, not the cheaper earlier ones


def test_quote_at_latency_boundary_is_eligible_and_one_before_is_not() -> None:
    at_boundary = [_q(200, "1500.00", "1500.05")]
    one_ms_early = [_q(199, "1490.00", "1490.05")]
    assert X.marketable_limit(_intent(), at_boundary, LAT).status == "FILLED"
    result = X.marketable_limit(_intent(), one_ms_early, LAT)
    assert (result.status, result.reason) == ("MISSED", "NO_QUOTE_AFTER_LATENCY")


def test_no_quote_after_latency_is_a_recorded_miss() -> None:
    r = X.marketable_limit(_intent(), [_q(-5, "1400.00", "1400.05")], LAT)
    assert (r.status, r.reason, r.filled_qty) == ("MISSED", "NO_QUOTE_AFTER_LATENCY", 0)


# ---- FE-0057 marketable limit ----

def test_buy_pays_the_ask_and_sell_receives_the_bid_never_the_mid() -> None:
    qs = [_q(300, "1500.00", "1500.10")]
    assert X.marketable_limit(_intent("BUY"), qs, LAT).avg_price == D("1500.10")
    assert X.marketable_limit(_intent("SELL"), qs, LAT).avg_price == D("1500.00")


def test_one_tick_worse_post_decision_quote_worsens_both_sides_of_fill_economics() -> None:
    touch = [_q(300, "1500.00", "1500.05")]
    adverse = [_q(300, "1499.95", "1500.10")]
    assert X.marketable_limit(_intent("BUY"), adverse, LAT).avg_price - \
        X.marketable_limit(_intent("BUY"), touch, LAT).avg_price == D("0.05")
    assert X.marketable_limit(_intent("SELL"), touch, LAT).avg_price - \
        X.marketable_limit(_intent("SELL"), adverse, LAT).avg_price == D("0.05")


def test_tick_rounding_goes_against_the_trader() -> None:
    qs = [_q(300, "1500.02", "1500.03")]
    assert X.marketable_limit(_intent("BUY"), qs, LAT).avg_price == D("1500.05")
    assert X.marketable_limit(_intent("SELL"), qs, LAT).avg_price == D("1500.00")


def test_visible_size_caps_the_fill_and_limit_protects_the_buyer() -> None:
    qs = [_q(300, "1500.00", "1500.05", aq=20)]
    r = X.marketable_limit(_intent(qty=50), qs, LAT)
    assert (r.status, r.filled_qty, r.reason) == ("PARTIAL", 20, "VISIBLE_SIZE_CAP")
    assert X.marketable_limit(_intent(limit="1500.00"), qs, LAT).reason == "LIMIT_NOT_MARKETABLE"


# ---- FE-0058 passive limit ----

def test_touching_the_limit_is_not_a_fill_and_queue_is_disclosed_unknown() -> None:
    trades = [(T0 + timedelta(seconds=1), D("1500.00"), 500)]
    r = X.passive_limit(_intent(limit="1500.00"), trades, LAT)
    assert r.status == "MISSED" and r.queue_position_known is False


def test_trade_through_fills_at_the_limit_not_the_better_print() -> None:
    trades = [(T0 + timedelta(seconds=1), D("1499.50"), 30), (T0 + timedelta(seconds=2), D("1499.40"), 100)]
    r = X.passive_limit(_intent(limit="1500.00", qty=50), trades, LAT)
    assert (r.status, r.filled_qty, r.avg_price) == ("FILLED", 50, D("1500.0000"))
    assert r.queue_position_known is False


# ---- FE-0059 adverse selection, bands, slices ----

def test_adverse_selection_moves_price_against_the_trader() -> None:
    base = X.ExecResult("FILLED", 10, D("1000"), "x")
    assert X.apply_adverse_selection(base, "BUY", D("10")).avg_price == D("1001.000")
    assert X.apply_adverse_selection(base, "SELL", D("10")).avg_price == D("999.000")


def test_price_band_rejects_and_freeze_quantity_slices() -> None:
    qs = [_q(300, "1500.00", "1500.05")]
    r = X.marketable_limit(_intent(), qs, LAT, price_band=(D("1000"), D("1400")))
    assert (r.status, r.reason) == ("REJECTED", "PRICE_BAND")
    assert X.slice_for_freeze(2500, 1000) == [1000, 1000, 500]
    with pytest.raises(X.ExecutionError):
        X.slice_for_freeze(10, 0)


# ---- FE-0060 effective-dated costs ----

def _sched() -> X.CostSchedule:
    old = X.CostRule(date(2026, 1, 1), D("3"), D("0.3"), D("1"), D("1"), D("0.1"), D("0.18"))
    new = X.CostRule(date(2026, 4, 1), D("3"), D("0.3"), D("1"), D("1.25"), D("0.1"), D("0.18"))
    return X.CostSchedule({"FIXTURE_EQ": (old, new)})


def test_cost_rule_is_selected_by_effective_date_and_side() -> None:
    s = _sched()
    sell_old = s.charges("FIXTURE_EQ", "SELL", D("100000"), date(2026, 3, 31))
    sell_new = s.charges("FIXTURE_EQ", "SELL", D("100000"), date(2026, 4, 1))
    assert sell_old["stt"] == D("10.00") and sell_new["stt"] == D("12.50")
    buy = s.charges("FIXTURE_EQ", "BUY", D("100000"), date(2026, 4, 1))
    assert buy["stamp"] == D("1.00") and sell_new["stamp"] == D("0.00")
    with pytest.raises(X.ExecutionError, match="no cost rule"):
        s.charges("FIXTURE_EQ", "BUY", D("1"), date(2025, 12, 31))


def test_total_is_rounded_once_from_unrounded_parts() -> None:
    s = X.CostSchedule({"S": (X.CostRule(date(2026, 1, 1), D("1"), D("1"), D("0"), D("0"), D("0"), D("0")),)})
    c = s.charges("S", "BUY", D("333.33"), date(2026, 1, 2))
    assert c["total"] == D("0.07")          # 0.0333+0.0333 rounded once
    assert c["brokerage"] + c["exchange"] == D("0.06")  # rounding each part first would give a different total


def test_cost_rule_models_all_effective_dated_cash_equity_components_and_hashes_table() -> None:
    rule = X.CostRule(
        date(2026, 11, 1), D("3"), D("0.3"), D("1"), D("1.25"), D("0.1"), D("0.18"),
        sebi_bps=D("0.01"), ipft_bps=D("0.005"), clearing_bps=D("0.02"),
        brokerage_max_inr=D("20"),
    )
    schedule = X.CostSchedule({"NSE_CASH": (rule,)})
    buy = schedule.charges("NSE_CASH", "BUY", D("100000"), date(2026, 11, 12))
    sell = schedule.charges("NSE_CASH", "SELL", D("100000"), date(2026, 11, 12))
    assert buy["brokerage"] == D("20.00")
    assert buy["sebi"] == D("0.10")
    assert buy["ipft"] == D("0.05")
    assert buy["clearing"] == D("0.20")
    assert buy["stt"] == D("10.00") and buy["stamp"] == D("1.00")
    assert sell["stt"] == D("12.50") and sell["stamp"] == D("0.00")
    assert buy["total"] == D("38.55") and sell["total"] == D("40.05")
    assert buy["total"] + sell["total"] == D("78.60")
    assert buy["schedule_sha256"] == schedule.sha256


def test_cost_rules_reject_invalid_rates_and_ambiguous_effective_dates() -> None:
    with pytest.raises(X.ExecutionError, match="finite nonnegative"):
        X.CostRule(date(2026, 1, 1), D("-1"), D("0"), D("0"), D("0"), D("0"), D("0"))
    rule = X.CostRule(date(2026, 1, 1), D("1"), D("1"), D("0"), D("0"), D("0"), D("0"))
    with pytest.raises(X.ExecutionError, match="duplicate effective dates"):
        X.CostSchedule({"S": (rule, rule)})


# ---- FE-0061 stress ----

def test_stress_reports_which_scenarios_stay_positive() -> None:
    stressed = X.stress_net(D("100"), D("20"), D("10"))
    assert stressed == {D(1): D("70"), D("1.5"): D("55"), D(2): D("40")}
    assert X.positive_scenarios(X.stress_net(D("40"), D("20"), D("10"))) == [D(1)]


# ---- FE-0062 options, FE-0064 FX ----

def test_a_structure_with_one_unexecutable_leg_is_not_executable() -> None:
    good = (D("10"), D("11"), 50, 50)
    assert X.multi_leg_executable([good, good]) == (True, "EXECUTABLE")
    assert X.multi_leg_executable([good, (None, D("9"), 50, 50)]) == (False, "LEG_1_ONE_SIDED")
    assert X.multi_leg_executable([good, (D("9"), D("10"), 0, 50)]) == (False, "LEG_1_NO_DEPTH")


def test_margin_comes_from_the_exchange_or_the_trade_is_blocked() -> None:
    assert X.margin_or_blocked(None) == ("BLOCKED_NO_OFFICIAL_MARGIN", None)
    assert X.margin_or_blocked(D("5000")) == ("OK", D("5000"))


def test_fx_execution_is_never_allowed() -> None:
    assert X.fx_execution_allowed() is False


# ---- FE-0065 fidelity ----

def test_fidelities_are_reported_separately_and_never_summed_together() -> None:
    rs = [
        X.ExecutionReceipt("o1", "SIMULATED_QUOTE", 10, D("100")),
        X.ExecutionReceipt("o2", "SIMULATED_BAR", 2, D("101")),
        X.ExecutionReceipt("o3", "OBSERVED_BROKER", 5, D("100")),
    ]
    assert X.totals_by_fidelity(rs) == {
        "SIMULATED_QUOTE": D("1000"), "SIMULATED_BAR": D("202"), "OBSERVED_BROKER": D("500")
    }
    with pytest.raises(X.MixedFidelity):
        X.combined_total(rs)
    assert X.combined_total([rs[0]]) == D("1000")
    assert X.combined_total([rs[1]]) == D("202")
    assert X.combined_total([rs[2]]) == D("500")
    with pytest.raises(X.ExecutionError):
        X.ExecutionReceipt("o3", "GUESSED", 1, D("1"))


def test_quote_arrival_shortfall_measures_only_displayed_quote_depth_not_broker_fills() -> None:
    arrival = X.Quote(T0 + timedelta(milliseconds=25), D("99.9"), D("100.2"), 20, 3)
    row = X.quote_arrival_shortfall(
        side="BUY", modeled_price=D("100.1"), requested_qty=5, decision_utc=T0,
        latency=timedelta(milliseconds=20), quote=arrival, source_sha256="a" * 64,
    )
    assert row["benchmark_type"] == "SUBSEQUENT_EXECUTABLE_QUOTE"
    assert row["fidelity"] == "SIMULATED_QUOTE" and row["broker_fill_observation"] is False
    assert row["measured_qty"] == 3 and row["depth_coverage"] == pytest.approx(0.6)
    assert D(row["shortfall_bp"]) == pytest.approx((D("100.2") / D("100.1") - 1) * D("10000"))
    report = X.quote_arrival_shortfall_report([row])
    assert report["broker_fill_observations"] == 0 and report["depth_coverage"] == pytest.approx(0.6)


def test_quote_arrival_shortfall_fails_closed_on_early_quotes_missing_depth_and_broker_labels() -> None:
    early = X.Quote(T0 + timedelta(milliseconds=10), D("99.9"), D("100.1"), 10, 10)
    with pytest.raises(X.ExecutionError, match="order arrival"):
        X.quote_arrival_shortfall(side="BUY", modeled_price=D("100"), requested_qty=1,
                                  decision_utc=T0, latency=timedelta(milliseconds=20),
                                  quote=early, source_sha256="a" * 64)
    empty = X.Quote(T0 + timedelta(milliseconds=20), D("99.9"), D("100.1"), 0, 0)
    with pytest.raises(X.ExecutionError, match="displayed quantity"):
        X.quote_arrival_shortfall(side="SELL", modeled_price=D("100"), requested_qty=1,
                                  decision_utc=T0, latency=timedelta(milliseconds=20),
                                  quote=empty, source_sha256="a" * 64)
    with pytest.raises(X.ExecutionError, match="cannot include broker-fill"):
        X.quote_arrival_shortfall_report([{"benchmark_type": "OBSERVED_BROKER_FILL",
                                           "fidelity": "OBSERVED_BROKER",
                                           "broker_fill_observation": True}])


# ---- FE-0066 uncertainty, FE-0067 identity, FE-0068 no-trade ----

def test_fill_bounds_bracket_the_expected_price() -> None:
    qs = [_q(300, "99", "101"), _q(400, "98", "103")]
    assert X.fill_bounds("BUY", qs) == (D("101"), D("102"), D("103"))
    assert X.fill_bounds("SELL", qs) == (D("99"), D("98.5"), D("98"))


def test_post_trade_identity_catches_a_cash_or_position_error() -> None:
    kw = dict(cash_before=D("10000"), position_before=0, side="BUY", quantity=10, price=D("100"), charges=D("1.50"))
    assert X.identity_holds(cash_after=D("8998.50"), position_after=10, **kw)
    assert not X.identity_holds(cash_after=D("9000.00"), position_after=10, **kw)
    assert not X.identity_holds(cash_after=D("8998.50"), position_after=9, **kw)


def test_outage_is_a_no_trade_not_a_favourable_fill() -> None:
    qs = [_q(300, "1500.00", "1500.05")]
    r = X.execute_or_no_trade(False, ["STALE_QUOTE", "QUOTE_GAP"], _intent(), qs, LAT)
    assert (r.status, r.filled_qty, r.reason) == ("NO_TRADE", 0, "QUOTE_GAP+STALE_QUOTE")
    assert X.execute_or_no_trade(True, [], _intent(), qs, LAT).status == "FILLED"
