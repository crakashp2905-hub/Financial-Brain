from datetime import date, datetime, timedelta

import pytest

from financial_brain.paper import intraday_runner as R
from financial_brain.providers import kite_data as KD


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
        return 101.6          # breaks above the 101 range high -> ORB long
    return 106.9              # beyond the 2R target


def fake_quotes(clock, day="2026-10-06", traded=True):
    def q(symbols, kind):
        px = price_at(clock.t)
        hi = max(101.0, px) if clock.t.hour * 60 + clock.t.minute >= 570 else 101.0
        return {s: {"last_price": px, "last_trade_time": f"{day} 09:20:00" if traded else "2026-10-01 15:29:59",
                    "ohlc": {"open": 100.0, "high": hi if clock.t.hour * 60 + clock.t.minute > 570 else 101.0,
                             "low": 99.0, "close": 100.0}} for s in symbols}
    return q


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(R, "universe", lambda n=200: [{"tradingsymbol": "X", "adv": 5e9}])
    monkeypatch.setattr(R, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(R, "daily_view", lambda **kw: (None, None, None))      # never touch the research database
    monkeypatch.setattr(R, "daily_up", lambda d: None)                     # nor for the daily trend
    from financial_brain.ingest import quote_archive as QA
    monkeypatch.setattr(QA, "PATH", tmp_path / "quotes.duckdb")          # nor the real quote archive
    return tmp_path


def test_a_live_day_opens_on_the_breakout_takes_the_target_and_writes_the_ledger(setup):
    c = Clock(datetime(2026, 10, 6, 9, 10, tzinfo=R.IST))
    out = R.run_live(quote_fn=fake_quotes(c), now_fn=c.now, sleep_fn=c.sleep, path=setup / "l.duckdb")
    assert out["status"] == "complete"
    con = R.ledger(setup / "l.duckdb")
    rows = con.execute("SELECT strategy, side, exit_reason, mode FROM trades").fetchall()
    assert ("ORB", "long", "target", "live") in rows
    assert (setup / "reports" / "2026-10-06-live.md").exists()


def test_live_runner_can_send_existing_kite_polls_to_forward_event_sink(setup, monkeypatch):
    from financial_brain.forward_edge.market_data import NormalizedEvent
    from financial_brain.forward_edge.durable import DurableJournal
    from financial_brain.forward_edge.modes import Mode
    from financial_brain.forward_edge.session_runner import ForwardRunConfig, ForwardSessionRunner

    c = Clock(datetime(2026, 10, 6, 9, 10, tzinfo=R.IST))
    monkeypatch.setattr(R, "universe", lambda n=200: [
        {"tradingsymbol": "X", "adv": 5e9}, {"tradingsymbol": "Y", "adv": 4e9}
    ])
    forward = ForwardSessionRunner(setup / "forward.jsonl")
    config = ForwardRunConfig(session_id="PAPER-2026-10-06", exchange_date="2026-10-06", cohort_id="C1",
                              strategy_sha256="a" * 64, risk_policy_sha256="b" * 64,
                              constraints_sha256="c" * 64, ledger_id="PAPER-LEDGER-1",
                              benchmark_id="NIFTY50", instruments=("NSE:X", "NSE:Y"), capital_inr="1000000",
                              mode=Mode.LIVE_PAPER)
    forward.begin(config)

    def quote(symbols, kind):
        assert kind == "full"
        return {s: {"timestamp": "invalid" if s == "NSE:Y" else c.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "last_price": 100.0,
                    "last_trade_time": "2026-10-06 09:20:00", "volume": 100,
                    "ohlc": {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
                    "depth": {"buy": [{"price": 99.95, "quantity": 10}],
                              "sell": [{"price": 100.05, "quantity": 12}]}}
                for s in symbols}

    out = R.run_live(quote_fn=quote, now_fn=c.now, sleep_fn=c.sleep, path=setup / "sink.duckdb",
                     poll=300, event_sink=lambda events: forward.append_events(config, events),
                     preflight_fn=lambda: {"approved": True, "status": "PASS", "checks": {"kite": True}},
                     preflight_sink=lambda result: forward.record_preflight(config, result),
                     rejection_sink=lambda rows: forward.record_data_rejections(config, rows))
    assert out["status"] == "complete"
    stored = [r["event"] for r in DurableJournal(setup / "forward.jsonl").records()
              if r["t"] == "MARKET_EVENT"]
    assert stored and all(r["kind"] == "QUOTE" for r in stored)
    assert all(dict(tuple(pair) for pair in r["payload"])["quote_fidelity"] == "EXECUTABLE" for r in stored)
    assert all(r["received_utc"] == r["available_utc"] for r in stored)
    assert all(isinstance(e, NormalizedEvent) for e in [
        NormalizedEvent(r["kind"], r["instrument"], datetime.fromisoformat(r["event_utc"]),
                        datetime.fromisoformat(r["received_utc"]), datetime.fromisoformat(r["available_utc"]),
                        tuple(tuple(p) for p in r["payload"])) for r in stored])
    journal_records = DurableJournal(setup / "forward.jsonl").records()
    assert any(r["t"] == "PREFLIGHT_RESULT" and r["result"]["approved"] for r in journal_records)
    data_rejections = [r for r in journal_records if r["t"] == "DATA_REJECTION"]
    assert data_rejections and all(r["instrument"] == "NSE:Y" for r in data_rejections)
    assert all(r["reason"] == "INVALID_EXCHANGE_TIMESTAMP" and len(r["event_sha256"]) == 64
               for r in data_rejections)
    finished = forward.run(config, [], decision_at=datetime(2026, 10, 6, 9, 30, tzinfo=R.IST),
                           decision_fn=lambda visible: {"side": "NONE", "observed": len(visible)},
                           risk_fn=lambda _: {"approved": True}, execution_fn=lambda *_: {})
    assert finished["status"] == "NO_TRADE"
    assert finished["event_count"] == len(stored)


def test_live_view_tracks_open_then_closed_trades_without_a_database_reader(setup):
    from financial_brain.paper.live_view import view_path
    c = Clock(datetime(2026, 10, 6, 9, 29, tzinfo=R.IST))
    times = iter([(9, 30), (9, 40), (10, 0), (15, 31)])
    snapshots = []
    path = setup / "viewer.duckdb"

    def advance(seconds):
        snapshots.append(view_path(path).read_text(encoding="utf-8"))
        hour, minute = next(times)
        c.t = c.t.replace(hour=hour, minute=minute)

    out = R.run_live(quote_fn=fake_quotes(c), now_fn=c.now, sleep_fn=advance, path=path)
    assert out["status"] == "complete"
    assert any("1 / 0" in s for s in snapshots)
    assert any("0 / 1" in s for s in snapshots)
    assert "Session complete" in view_path(path).read_text(encoding="utf-8")


def test_a_holiday_is_detected_and_the_run_ends_quietly(setup):
    c = Clock(datetime(2026, 10, 2, 9, 10, tzinfo=R.IST))
    out = R.run_live(quote_fn=fake_quotes(c, day="2026-10-02", traded=False), now_fn=c.now, sleep_fn=c.sleep,
                     path=setup / "l.duckdb")
    assert out["status"] == "closed"
    assert R.ledger(setup / "l.duckdb").execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0


def test_a_failing_feed_aborts_after_the_limit_rather_than_spinning_all_day(setup):
    c = Clock(datetime(2026, 10, 6, 9, 20, tzinfo=R.IST))

    def broken(symbols, kind):
        raise RuntimeError("token expired")
    out = R.run_live(quote_fn=broken, now_fn=c.now, sleep_fn=c.sleep, path=setup / "l.duckdb", max_failures=5)
    assert out["status"] == "aborted" and "token expired" in out["why"]


def test_missing_session_retries_then_resumes_without_aborting(setup):
    c = Clock(datetime(2026, 10, 6, 9, 27, tzinfo=R.IST))
    quotes = fake_quotes(c)
    attempts = 0

    def late_login(symbols, kind):
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise KD.KiteNotConfigured("no token")
        return quotes(symbols, kind)

    def finish_after_recovery(seconds):
        c.sleep(seconds)
        if c.t.hour == 9 and c.t.minute >= 32:
            c.t = datetime(2026, 10, 6, 15, 31, tzinfo=R.IST)

    out = R.run_live(quote_fn=late_login, now_fn=c.now, sleep_fn=finish_after_recovery,
                     path=setup / "l.duckdb", max_failures=1)
    assert out["status"] == "complete" and out["polls"] > 0
    assert attempts > 2


def test_missing_session_at_close_is_not_recorded_as_complete(setup):
    c = Clock(datetime(2026, 10, 6, 15, 29, tzinfo=R.IST))

    def missing(symbols, kind):
        raise KD.KiteAuthError("session rejected")

    out = R.run_live(quote_fn=missing, now_fn=c.now, sleep_fn=c.sleep,
                     path=setup / "l.duckdb", max_failures=1)
    assert out["status"] == "session_unavailable" and out["polls"] == 0
    row = R.ledger(setup / "l.duckdb").execute("SELECT status FROM runs WHERE mode = 'live'").fetchone()
    assert row == ("session_unavailable",)


def test_bar_path_puts_the_low_first_on_an_up_bar():
    assert R.path_of_bar(10, 12, 9, 11) == (10, 9, 12, 11)
    assert R.path_of_bar(10, 12, 9, 9.5) == (10, 12, 9, 9.5)


def test_replay_runs_the_same_engine_on_minute_candles_and_labels_it(setup):
    day = date(2026, 10, 5)

    def candles(tok, start, end, interval):
        if interval == "day":
            return [{"ts": "2026-10-01T00:00:00+0530", "close": 100.0}, {"ts": "2026-10-05T00:00:00+0530", "close": 105.0}]
        bars = []
        for k in range(360):
            t = datetime(2026, 10, 5, 9, 15) + timedelta(minutes=k)
            px = price_at(t)
            bars.append({"ts": t.strftime("%Y-%m-%dT%H:%M:%S+0530"), "open": px, "high": px + 0.05, "low": px - 0.05,
                         "close": px, "volume": 1000})
        return bars
    out = R.run_replay(day, candles_fn=candles, token_fn=lambda s: 1, path=setup / "l.duckdb")
    assert out["symbols"] == 1 and out["summary"]["ORB"]["trades"] == 1
    ev = R.evidence(R.ledger(setup / "l.duckdb"))
    assert ev[0]["mode"] == "replay" and ev[0]["status"] == "COLLECTING"


def test_a_late_start_takes_the_opening_range_from_candles_not_from_the_days_high_and_low(setup):
    c = Clock(datetime(2026, 10, 6, 9, 50, tzinfo=R.IST))         # logged in late
    asked = []

    def candles(tok, start, end, interval):
        asked.append((start, end))
        return [{"ts": f"2026-10-06T09:{15 + k:02d}:00+0530", "open": 100.0, "high": 101.0, "low": 99.0,
                 "close": 100.0} for k in range(15)]

    def quotes(symbols, kind):                          # by now the day's high is 107: the wrong range
        return {s: {"last_price": 101.6 if c.t.minute < 55 else 106.9, "last_trade_time": "2026-10-06 09:49:00",
                    "ohlc": {"open": 100.0, "high": 107.0, "low": 99.0, "close": 100.0}} for s in symbols}
    R.run_live(quote_fn=quotes, now_fn=c.now, sleep_fn=c.sleep, path=setup / "l.duckdb",
               candles_fn=candles, token_fn=lambda s: 1)
    assert asked, "the exact range must be fetched for a late start"
    rows = R.ledger(setup / "l.duckdb").execute("SELECT stop FROM trades WHERE strategy = 'ORB'").fetchall()
    assert rows and rows[0][0] == 99.0                  # the stop is the true 09:15-09:29 low


def test_started_after_the_close_or_on_a_weekend_it_records_nothing(setup):
    for start in (datetime(2026, 10, 6, 19, 30, tzinfo=R.IST), datetime(2026, 10, 10, 10, 0, tzinfo=R.IST)):
        c = Clock(start)
        out = R.run_live(quote_fn=fake_quotes(c), now_fn=c.now, sleep_fn=c.sleep, path=setup / "l.duckdb")
        assert out["status"] == "not_a_session"
    assert not (setup / "l.duckdb").exists()


def board_daily():
    import pandas as pd
    return (pd.DataFrame({"ticker": ["X"], "z": [1.5], "alpha": [0.004], "cost": [0.003], "net": [0.001],
                          "vol_60": [0.25], "adv20": [5e9]}), date(2026, 10, 5), None)


def test_the_live_day_posts_a_0930_board_and_resolves_it_at_the_close(setup):
    c = Clock(datetime(2026, 10, 6, 9, 10, tzinfo=R.IST))
    R.run_live(quote_fn=fake_quotes(c), now_fn=c.now, sleep_fn=c.sleep, path=setup / "l.duckdb", daily_fn=board_daily)
    con = R.ledger(setup / "l.duckdb")
    row = con.execute("SELECT symbol, top, clears, ret_close FROM board WHERE mode = 'live'").fetchone()
    assert row[0] == "X" and row[1] is True and row[2] is False and row[3] is not None
    text = (setup / "reports" / "2026-10-06-board-live.md").read_text(encoding="utf-8")
    assert "Nothing clears costs today" in text
    assert "top five beat" in (setup / "reports" / "2026-10-06-live.md").read_text(encoding="utf-8")


def test_the_replay_posts_a_board_from_the_candles(setup):
    def candles(tok, start, end, interval):
        if interval == "day":
            return [{"ts": "2026-10-01T00:00:00+0530", "close": 100.0}]
        return [{"ts": (datetime(2026, 10, 5, 9, 15) + timedelta(minutes=k)).strftime("%Y-%m-%dT%H:%M:%S+0530"),
                 "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 10} for k in range(360)]
    R.run_replay(date(2026, 10, 5), candles_fn=candles, token_fn=lambda s: 1, path=setup / "l.duckdb",
                 daily_fn=board_daily)
    con = R.ledger(setup / "l.duckdb")
    assert con.execute("SELECT COUNT(*) FROM board WHERE mode = 'replay' AND ret_close IS NOT NULL").fetchone()[0] == 1


def test_live_fills_use_the_order_book_from_full_quotes(setup):
    c = Clock(datetime(2026, 10, 6, 9, 10, tzinfo=R.IST))
    base = fake_quotes(c)
    kinds = []

    def q(symbols, kind):
        kinds.append(kind)
        out = base(symbols, kind)
        for v in out.values():
            v["depth"] = {"buy": [{"price": v["last_price"] - 0.05}], "sell": [{"price": v["last_price"] + 0.05}]}
        return out
    R.run_live(quote_fn=q, now_fn=c.now, sleep_fn=c.sleep, path=setup / "l.duckdb")
    assert set(kinds) == {"full"}
    row = R.ledger(setup / "l.duckdb").execute(
        "SELECT entry_px, entry_decision_px, spread_bp, shortfall_bp FROM trades").fetchone()
    assert row[0] == pytest.approx(row[1] + 0.05) and row[2] > 0 and row[3] > 0
