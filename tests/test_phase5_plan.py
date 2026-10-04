import inspect
from datetime import date

import pytest

from financial_brain.decisions import plan as P


def base(**kw):
    a = dict(ticker="X", price=1000.0, vol_annual=0.25, adv_inr=5e9, capital_inr=1_000_000, style="position",
             as_of=date(2026, 10, 5))
    a.update(kw)
    return P.plan(**a)


def test_size_is_the_risk_budget_over_the_stop_distance():
    p = base()
    stop_pct = 3.0 * 0.25 / (252 ** 0.5) * (20 ** 0.5)
    expected = int(0.005 * 1_000_000 / (stop_pct * 1000.0))
    assert p["shares"] == expected and p["sized_by"] == "risk budget"
    assert p["risk_if_stopped_pct_of_account"] <= 0.005 + 1e-9


def test_a_wider_stop_means_a_smaller_position_for_the_same_risk():
    lo = base(vol_annual=0.15)
    hi = base(vol_annual=0.45)
    assert hi["shares"] < lo["shares"]
    assert hi["risk_if_stopped_inr"] == pytest.approx(lo["risk_if_stopped_inr"], rel=0.1)


def test_weight_and_liquidity_caps_bind_when_they_should():
    assert base(vol_annual=0.01, max_weight=0.05)["sized_by"] == "maximum weight"
    p = base(adv_inr=3e5, vol_annual=0.05)
    assert p["sized_by"] == "liquidity" and p["notional"] <= 0.10 * 3e5 + 1000


def test_a_tiny_account_gets_too_small_not_a_silly_order():
    p = base(capital_inr=20_000)
    assert p["status"] == "TOO_SMALL" and "minimum" in p["note"]


def test_styles_carry_their_own_evidence_and_the_plan_says_so_first():
    assert base(style="position")["status"] == "PLAN"
    sw = base(style="swing")
    assert sw["edge_status"] == P.NONE_DEMONSTRATED and sw["status"] == "PLAN_WITHOUT_EDGE"
    assert "paying costs" in sw["note"]


def test_swing_and_intraday_horizons_and_costs():
    assert base(style="swing")["horizon_sessions"] == 7
    i = base(style="intraday")
    assert i["horizon_sessions"] == 0 and i["time_exit_on"] is None and "15:15" in "; ".join(i["exits"])
    assert i["round_trip_cost_pct"] < base(style="swing")["round_trip_cost_pct"]       # intraday STT is on the sell only


def test_time_exit_skips_weekends():
    p = base(as_of=date(2026, 10, 2))            # a Friday
    assert p["time_exit_on"].weekday() < 5 and p["time_exit_on"] > date(2026, 12, 1)


def test_shorts_are_intraday_only_and_bad_inputs_refused():
    assert base(style="intraday", side="short")["stop_price"] > 1000
    with pytest.raises(P.PlanError):
        base(style="swing", side="short")
    with pytest.raises(P.PlanError):
        base(style="scalp")
    with pytest.raises(P.PlanError):
        base(risk_per_trade=0.2)
    with pytest.raises(P.PlanError):
        base(price=-1)


def test_the_text_says_it_is_information_and_the_module_cannot_send_anything():
    assert "not advice" in P.render(base())
    src = inspect.getsource(P).lower()
    for w in ("urllib", "requests", "socket", "kiteconnect", "place_order", "/orders"):
        assert w not in src
