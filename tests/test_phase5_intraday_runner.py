from datetime import date, datetime, timedelta

import pytest

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
