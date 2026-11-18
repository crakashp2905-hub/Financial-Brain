"""The live-v2 cohort through the real runner: its own ledger, the declined entry never traded, live-v1 untouched, day context honoured."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from financial_brain.learning import activation as AC
from financial_brain.learning import shadow as SH
from financial_brain.paper import intraday_runner as R


class Clock:
    def __init__(self, start):
        self.t = start

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += timedelta(seconds=s)


def price_at(t):
    m = t.hour * 60 + t.minute
    if m < 570:
        return 100.0
    if m < 580:
        return 100.5
    if m < 600:
        return 101.6          # breaks above the 101 range high: ORB long
    return 106.9              # beyond the 2R target


def fake_quotes(clock, day="2026-10-06"):
    def q(symbols, kind):
        px = price_at(clock.t)
        hi = max(101.0, px) if clock.t.hour * 60 + clock.t.minute >= 570 else 101.0
        return {s: {"last_price": px, "last_trade_time": f"{day} 09:20:00",
                    "ohlc": {"open": 100.0, "high": hi if clock.t.hour * 60 + clock.t.minute > 570 else 101.0, "low": 99.0, "close": 100.0}}
                for s in symbols}
    return q


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(R, "universe", lambda n=200: [{"tradingsymbol": "X", "adv": 5e9}])
    monkeypatch.setattr(R, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(R, "daily_view", lambda **kw: (None, None, None))
    monkeypatch.setattr(R, "daily_up", lambda d: None)
    from financial_brain.ingest import quote_archive as QA
    monkeypatch.setattr(QA, "PATH", tmp_path / "quotes.duckdb")
    return tmp_path


def _rule(dimension, value, strategy="ORB"):
    return SH.SkipRule(id="SK-0001", strategy=strategy, dimension=dimension, value=value, prereg="PRE-REGISTERED x",
                       active_from="2026-01-01", status="ACTIVE")


def _run(setup, rules, **kw):
    c = Clock(datetime(2026, 10, 6, 9, 10, tzinfo=R.IST))
    kw2 = AC.cohort_args(rules) if rules is not None else {}
    kw2.update(kw)
    out = R.run_live(quote_fn=fake_quotes(c), now_fn=c.now, sleep_fn=c.sleep, path=setup / "paper_live.duckdb", **kw2)
    return out


def _trades(path):
    con = R.ledger(path)
    try:
        return con.execute("SELECT strategy, side, exit_reason, net_pnl FROM trades").fetchall()
    finally:
        con.close()


def test_a_rule_that_matches_declines_the_entry_in_its_own_ledger_and_leaves_v1_alone(setup):
    assert _run(setup, None)["status"] == "complete"
    v1 = _trades(setup / "paper_live.duckdb")
    assert [t[:2] for t in v1] == [("ORB", "long")]
    out = _run(setup, [_rule("direction", "long")])
    assert out["status"] == "complete"
    (v2_path,) = AC.v2_ledgers(setup)
    assert v2_path.name.startswith("paper_live-live-v2-") and _trades(v2_path) == []
    assert _trades(setup / "paper_live.duckdb") == v1                              # live-v1's ledger is untouched


def test_a_rule_that_does_not_match_changes_nothing(setup):
    _run(setup, None)
    v1 = _trades(setup / "paper_live.duckdb")
    _run(setup, [_rule("direction", "short")])
    (v2_path,) = AC.v2_ledgers(setup)
    assert _trades(v2_path) == v1


def test_the_days_regime_reaches_the_filter_through_the_runner(setup):
    asked = []

    def ctx(day):
        asked.append(day)
        return {"regime_prev": "RISK_OFF", "vix": 17.0}

    _run(setup, [_rule("regime_prev", "regime RISK_OFF")], day_context_fn=ctx)
    (v2_path,) = AC.v2_ledgers(setup)
    assert asked == ["2026-10-06"] and _trades(v2_path) == []
    # and a regime the rule is not about lets the same entry through
    for p in AC.v2_ledgers(setup):
        p.unlink()
    _run(setup, [_rule("regime_prev", "regime RISK_ON")], day_context_fn=ctx)
    (v2_path,) = AC.v2_ledgers(setup)
    assert [t[:2] for t in _trades(v2_path)] == [("ORB", "long")]


def test_the_cohort_is_a_different_ledger_for_different_rules(setup):
    _run(setup, [_rule("direction", "long")])
    _run(setup, [_rule("weekday", "Tue")])
    assert len(AC.v2_ledgers(setup)) == 2


def test_replay_accepts_the_same_cohort_arguments(setup):
    from datetime import date

    def candles(tok, start, end, interval="minute"):
        if interval == "day":
            return [{"ts": "2026-10-02T00:00:00", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1}] * 3
        rows = []
        t = datetime(2026, 10, 6, 9, 15)
        while t <= datetime(2026, 10, 6, 15, 29):
            px = price_at(t)
            rows.append({"ts": t.strftime("%Y-%m-%dT%H:%M:00"), "open": px, "high": max(px, 101.0 if t.hour * 60 + t.minute < 570 else px),
                         "low": min(px, 99.0 if t.hour * 60 + t.minute < 570 else px), "close": px, "volume": 1000})
            t += timedelta(minutes=1)
        return rows
    out = R.run_replay(date(2026, 10, 6), candles_fn=candles, token_fn=lambda s: 1, path=setup / "paper_live.duckdb",
                       **AC.cohort_args([_rule("direction", "long")]))
    assert out["status"] in ("complete", "replayed", "ok") or "day" in out
    assert len(AC.v2_ledgers(setup)) == 1


def test_a_failed_day_context_lookup_fails_open_and_the_cohort_trades_like_v1(setup):
    def broken(day):
        raise RuntimeError("research database is locked")

    _run(setup, None)
    v1 = _trades(setup / "paper_live.duckdb")
    out = _run(setup, [_rule("regime_prev", "regime RISK_OFF")], day_context_fn=broken)
    assert out["status"] == "complete"
    (v2_path,) = AC.v2_ledgers(setup)
    assert _trades(v2_path) == v1
