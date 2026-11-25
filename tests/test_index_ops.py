from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.forecasting.index_ops import (
    ForecastLog,
    LogisticDirection,
    OpsError,
    OptionableIndexMaster,
    decision_times,
    eod_audit,
    forecast_or_unavailable,
    option_card,
)

IST = timezone(timedelta(hours=5, minutes=30))
OPEN = datetime(2026, 11, 17, 9, 15, tzinfo=IST)


def test_optionable_master_is_dated():
    m = OptionableIndexMaster()
    m.add(date(2026, 11, 1), {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "NIFTYFPI"}, "NSE")
    assert m.optionable("NIFTY", date(2026, 11, 17)) and not m.optionable("NIFTYIT", date(2026, 11, 17))
    with pytest.raises(OpsError):
        m.optionable("NIFTY", date(2026, 1, 1))


def test_decision_times_one_per_interval():
    t = decision_times(OPEN, OPEN.replace(hour=15, minute=30))
    assert t[0] == OPEN + timedelta(minutes=15) and len(t) == 25


def test_unavailable_when_inputs_stale_or_missing():
    now = OPEN + timedelta(minutes=30)
    s, why = forecast_or_unavailable({"index": now, "option_chain": None}, now, timedelta(seconds=30),
                                     {"index", "option_chain"})
    assert s == "UNAVAILABLE" and why == ["missing option_chain"]
    s, why = forecast_or_unavailable({"index": now - timedelta(minutes=2)}, now, timedelta(seconds=30), {"index"})
    assert s == "UNAVAILABLE"
    assert forecast_or_unavailable({"index": now}, now, timedelta(seconds=30), {"index"})[0] == "FORECAST"


def test_forecast_log_hash_chain_and_duplicates():
    log = ForecastLog()
    ev = dict(forecast_id="F1", index_id="NIFTY", bar_cutoff="09:30", config_hash="c", prediction=0.6)
    log.append(ev); log.append({**ev, "forecast_id": "F2"})
    assert log.verify()
    with pytest.raises(OpsError):
        log.append(ev)
    log.events[0]["prediction"] = 0.9
    assert not log.verify()


def test_logistic_learns_simple_signal():
    rng = random.Random(3)
    x = [[rng.gauss(0, 1)] for _ in range(300)]
    y = [1 if v[0] > 0 else 0 for v in x]
    m = LogisticDirection("v1").fit(x, y)
    assert m.p_up([2.0]) > 0.8 and m.p_up([-2.0]) < 0.2


def test_option_card_links_exit_and_max_loss():
    c = option_card(forecast_id="F1", token="T", kind="CE", strike=24000, expiry=date(2026, 11, 24),
                    lots=2, lot_size=75, entry_low=100, entry_high=102, sl_premium=80, tp_premium=140,
                    underlying_invalidation=23900, time_stop=OPEN + timedelta(hours=1),
                    quote_ttl=timedelta(seconds=10), expected_net=150)
    assert c["exit"]["close_action"] == "SELL_TO_CLOSE" and c["max_planned_loss"] == 22 * 150
    with pytest.raises(OpsError):
        option_card(forecast_id="F", token="T", kind="CE", strike=1, expiry=date(2026, 11, 24), lots=1,
                    lot_size=1, entry_low=100, entry_high=102, sl_premium=101, tp_premium=140,
                    underlying_invalidation=1, time_stop=OPEN, quote_ttl=timedelta(seconds=1),
                    expected_net=0)


def test_eod_audit_lists_unresolved():
    r = eod_audit([dict(index_id="NIFTY", state="CLOSED", pnl=100.0, cost=20.0, forecast_id="a"),
                   dict(index_id="NIFTY", state="NO_TRADE", forecast_id="b"),
                   dict(index_id="BANKNIFTY", state="UNPRICED", forecast_id="c")])
    assert r["net_pnl"] == 80.0 and r["unresolved"] == ["c"]
    assert r["by_index"]["NIFTY"] == {"CLOSED": 1, "NO_TRADE": 1}
