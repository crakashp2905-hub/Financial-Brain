import inspect
from datetime import date

import pytest

from financial_brain.costs import calculator as CC
from financial_brain.costs import tax as T
from financial_brain.costs.india import CostModel, Segment, Side
from financial_brain.evaluation import after_tax as AT


# ------------------------------------------------------------------ rates and classification
def test_rates_include_cess_and_classification_follows_holding_and_segment():
    r = T.TaxRules()
    assert r.short_rate == pytest.approx(0.208) and r.long_rate == pytest.approx(0.13)
    assert r.speculative_rate == pytest.approx(0.312)
    assert T.classify(30) == T.SHORT and T.classify(365) == T.SHORT and T.classify(366) == T.LONG
    assert T.classify(0, intraday=True) == T.INTRADAY
    with pytest.raises(T.TaxError):
        T.classify(-1)


def test_a_single_gain_is_taxed_by_kind_and_a_loss_pays_nothing():
    assert T.tax_on_gain(100_000, T.SHORT) == pytest.approx(20_800)
    assert T.tax_on_gain(100_000, T.LONG) == 0.0                          # inside the Rs 1.25 lakh exemption
    assert T.tax_on_gain(225_000, T.LONG) == pytest.approx(100_000 * 0.13)
    assert T.tax_on_gain(100_000, T.INTRADAY, T.TaxRules(slab=0.20)) == pytest.approx(100_000 * 0.20 * 1.04)
    assert T.tax_on_gain(-5_000, T.SHORT) == 0.0


def test_financial_year_runs_april_to_march():
    assert T.fiscal_year(date(2026, 3, 31)) == 2026 and T.fiscal_year(date(2026, 4, 1)) == 2027


# ------------------------------------------------------------------ ledger: set-off and carry-forward
def test_a_short_term_loss_offsets_short_then_long_gains():
    led = T.Ledger()
    led.add(date(2025, 5, 1), -50_000, T.SHORT)
    led.add(date(2025, 6, 1), 60_000, T.SHORT)
    led.add(date(2025, 7, 1), 300_000, T.LONG)
    tax = led.close_year(2026)
    assert tax == pytest.approx(10_000 * 0.208 + (300_000 - 125_000) * 0.13)


def test_a_loss_carries_forward_and_shelters_the_next_years_gain_then_expires():
    led = T.Ledger()
    led.add(date(2025, 5, 1), -100_000, T.SHORT)
    assert led.close_year(2026) == 0.0
    led.add(date(2026, 6, 1), 150_000, T.SHORT)
    assert led.close_year(2027) == pytest.approx(50_000 * 0.208)
    old = T.Ledger()
    old.add(date(2016, 5, 1), -100_000, T.SHORT)
    old.close_year(2017)
    old.add(date(2026, 6, 1), 100_000, T.SHORT)
    assert old.close_year(2027) == pytest.approx(100_000 * 0.208)        # nine years on: expired


def test_speculative_losses_do_not_shelter_capital_gains():
    led = T.Ledger(T.TaxRules(slab=0.30))
    led.add(date(2025, 5, 1), -200_000, T.INTRADAY)
    led.add(date(2025, 6, 1), 100_000, T.SHORT)
    assert led.close_year(2026) == pytest.approx(100_000 * 0.208)


# ------------------------------------------------------------------ active vs passive on the same returns
def dates(n=60):
    return [date.fromordinal(date(2018, 4, 2).toordinal() + 28 * i) for i in range(n)]


def test_with_no_gains_there_is_no_tax_and_both_end_equal():
    d, r = dates(), [0.0] * 60
    assert AT.simulate_active(d, r)["tax_paid"] == 0.0
    assert AT.simulate_passive(d, r)["terminal"] == pytest.approx(1_000_000)


def test_the_same_positive_returns_end_lower_when_taxed_every_year_than_when_deferred():
    d, r = dates(), [0.012] * 60
    act, pas = AT.simulate_active(d, r), AT.simulate_passive(d, r)
    assert act["tax_paid"] > 0 and pas["terminal"] > act["terminal"]
    assert act["cagr"] < AT.pre_tax_cagr(d, r) and pas["cagr"] < AT.pre_tax_cagr(d, r)
    assert pas["cagr"] - act["cagr"] > 0.01                               # deferral is worth over a point of CAGR


def test_a_losing_year_shelters_the_next_years_gain_in_the_account_simulation():
    d = dates(26)                                         # 13 periods in FY2019, 13 in FY2020
    r = [-0.03] * 13 + [0.05] * 13
    w1 = 1_000_000 * 0.97 ** 13
    loss = 1_000_000 - w1
    gain2 = w1 * (1.05 ** 13 - 1)
    out = AT.simulate_active(d, r)
    assert out["tax_paid"] == pytest.approx((gain2 - loss) * 0.208, rel=1e-9)
    assert out["tax_paid"] < gain2 * 0.208


def test_bad_inputs_are_refused():
    with pytest.raises(AT.AfterTaxError):
        AT.simulate_active(dates(3), [0.0] * 3)
    with pytest.raises(AT.AfterTaxError):
        AT.simulate_active(list(reversed(dates(10))), [0.0] * 10)


# ------------------------------------------------------------------ cost model and calculator
def test_the_depository_fee_is_on_a_delivery_sell_only_and_regressive():
    m = CostModel()
    assert m.one_way(turnover=100_000, side=Side.SELL, segment=Segment.DELIVERY)["dp"] == pytest.approx(15.34)
    assert m.one_way(turnover=100_000, side=Side.BUY, segment=Segment.DELIVERY)["dp"] == 0.0
    assert m.one_way(turnover=100_000, side=Side.SELL, segment=Segment.INTRADAY)["dp"] == 0.0
    small, big = CC.quote(order_inr=10_000), CC.quote(order_inr=10_000_000)
    assert small["fees_bp"] > big["fees_bp"] + 5                          # flat fees bite small orders


def test_charges_are_exact_and_the_range_orders_low_before_high():
    q = CC.quote(order_inr=100_000, adv_inr=3e9, vol_annual=0.2)
    assert q["legs"]["stt"] == (100.0, 100.0) and q["legs"]["stamp"][0] == pytest.approx(15.0)
    assert q["fees"] == pytest.approx(222.25 + 15.34, abs=0.05)
    assert q["total_low"] <= q["total_high"] and q["bp_low"] < q["bp_high"]


def test_without_turnover_the_range_collapses_and_says_so():
    q = CC.quote(order_inr=100_000)
    assert q["impact_low"] == q["impact_high"] and "bucket" in q["impact_basis"]


def test_profit_after_cost_and_tax_is_kept_in_proportion():
    q = CC.quote(order_inr=500_000, adv_inr=3e9, vol_annual=0.2, hold_sessions=20, expected_gross_return=0.05)
    p = q["profit"]
    assert p["kind"] == T.SHORT and p["gross_pnl"] == pytest.approx(25_000)
    lo = p["low"]
    assert lo["tax"] == pytest.approx(lo["net_pre_tax"] * 0.208, rel=1e-9)
    assert lo["after_tax"] == pytest.approx(lo["net_pre_tax"] - lo["tax"])
    assert 0.5 < lo["kept_pct_of_gross"] < 0.8


def test_a_gain_smaller_than_the_cost_pays_no_tax_and_loses_money():
    q = CC.quote(order_inr=500_000, adv_inr=3e9, vol_annual=0.2, expected_gross_return=0.001)
    assert q["profit"]["low"]["net_pre_tax"] < 0 and q["profit"]["low"]["tax"] == 0.0


def test_intraday_profit_is_taxed_at_the_slab_and_long_holds_use_the_long_rate():
    i = CC.quote(order_inr=500_000, segment=Segment.INTRADAY, expected_gross_return=0.02, rules=T.TaxRules(slab=0.20))
    assert i["profit"]["kind"] == T.INTRADAY
    assert i["profit"]["low"]["tax"] == pytest.approx(i["profit"]["low"]["net_pre_tax"] * 0.20 * 1.04)
    lg = CC.quote(order_inr=5_000_000, hold_sessions=300, expected_gross_return=0.30)
    assert lg["profit"]["kind"] == T.LONG


def test_account_table_cost_falls_then_impact_takes_over():
    rows = CC.account_table()
    assert rows[0]["fees_bp"] > rows[3]["fees_bp"]                        # tiny orders pay the flat fee
    assert rows[-1]["impact_bp_low"] > rows[2]["impact_bp_low"]           # huge orders pay impact
    assert "Information, not advice" in CC.render(CC.quote(order_inr=100_000))


def test_bad_inputs_and_no_network_code():
    with pytest.raises(CC.CalcError):
        CC.quote(order_inr=0)
    with pytest.raises(CC.CalcError):
        CC.quote(order_inr=1000, bucket="huge")
    for mod in (CC, T, AT):
        src = inspect.getsource(mod).lower()
        for w in ("urllib", "requests", "socket", "/orders", "place_order"):
            assert w not in src
