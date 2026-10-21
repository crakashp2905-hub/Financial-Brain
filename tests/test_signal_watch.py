"""The notification surface cannot turn unsupported forecasts into trading claims."""
from datetime import date, datetime, timedelta
from unittest.mock import patch

import duckdb
import pytest

from financial_brain import alerts as A
from financial_brain.evaluation import oos_monitor as OM
from financial_brain.evaluation import standard as S
from financial_brain.services import trading as T
from financial_brain.signals import watch as W


def test_failed_historical_trial_cannot_be_promoted_by_forward_result():
    trial = OM.REGISTERED[0]
    con = duckdb.connect()
    with (patch.object(T.duckdb, "connect", return_value=con),
          patch.object(OM, "report", return_value=[{"strategy": trial.strategy, "verdict": "CONFIRMED"}]),
          patch.object(S, "ledger", return_value=[{"name": trial.trial_name, "prereg": trial.prereg,
                                                  "verdict": "FAIL"}]),
          patch("financial_brain.evaluation.small_tier.assess", return_value={"qualifies": False})):
        assert T.verdict(trial.strategy) == "FAIL"


def test_exact_candidate_can_be_confirmed_but_failed_forward_cannot():
    trial = OM.REGISTERED[0]
    row = {"name": trial.trial_name, "prereg": trial.prereg, "verdict": "CANDIDATE"}
    for forward, expected in (("CONFIRMED", "CONFIRMED"), ("FAILED", "FAIL")):
        con = duckdb.connect()
        with (patch.object(T.duckdb, "connect", return_value=con),
              patch.object(OM, "report", return_value=[{"strategy": trial.strategy, "verdict": forward}]),
              patch.object(S, "ledger", return_value=[row])):
            assert T.verdict(trial.strategy) == expected


def test_option_scenario_uses_ask_and_lot_and_reports_premium_risk():
    class Paths:
        model = "test-baseline"

        def terminal(self):
            return [90.0, 100.0, 120.0]

    result = W.option_scenario(Paths(), symbol="ABC", expiry="2026-11-01", strike=100.0,
                               kind="CE", ask=5.0, bid=4.0, lot=10)
    assert result["break_even"] == 105.0
    assert result["max_premium_loss_inr"] == 50.0
    assert result["p_profit_before_fees"] == pytest.approx(1 / 3)
    assert result["mean_payoff_before_fees_inr"] == pytest.approx(((-5 - 5 + 15) * 10) / 3)


def test_stale_quotes_cannot_produce_watch_or_sell_alert():
    now = datetime(2026, 10, 22, 10, 0, tzinfo=W.IST)
    old = {"last_price": 80.0, "timestamp": now - timedelta(minutes=5)}
    out, _ = W.build_alerts([], {}, {"NSE:ABC": old}, [],
                            [{"symbol": "ABC", "stop": 90.0}], today=now.date(), now=now)
    assert out == []


def test_watch_alerts_never_call_unconfirmed_stock_or_option_a_buy():
    today = date(2026, 10, 22)
    now = datetime(2026, 10, 22, 10, 0, tzinfo=W.IST)
    history = [100.0 + (i % 10) for i in range(150)]
    quote = {"last_price": 110.0, "timestamp": now}
    option_quote = {"timestamp": now, "depth": {"buy": [{"price": 4.0}],
                                                  "sell": [{"price": 5.0}]}}
    contract = {"name": "ABC", "instrument_type": "CE", "expiry": "2026-11-01",
                "strike": 110.0, "tradingsymbol": "ABC-CE", "lot_size": 10}
    opportunities = [{"ticker": "ABC", "timestamp": "2026-10-21", "horizon": 20,
                      "alpha": {"net": 0.02}, "evidence": {"status": "HISTORICAL"}}]
    alerts, observations = W.build_alerts(
        opportunities, {"ABC": history}, {"NSE:ABC": quote, "NFO:ABC-CE": option_quote},
        [contract], [], today=today, now=now)
    assert len(alerts) == 2 and len(observations) == 2
    assert alerts[0]["title"].startswith("WATCH BUY")
    assert "No validated buy signal" in alerts[0]["body"]
    assert alerts[1]["title"].startswith("OPTION WATCH")
    assert "no validated options edge" in alerts[1]["body"]


def test_telegram_requires_explicit_configuration(monkeypatch):
    monkeypatch.delenv("FB_ALERT_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("FB_ALERT_TELEGRAM_CHAT_ID", raising=False)
    assert not A.telegram_configured()
    assert not A.telegram("title", "body")
