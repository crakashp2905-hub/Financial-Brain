"""The depth and open-interest archive: collection survives a late login and a holiday, never double-writes, and its
health report tells a full day from a gap. Microstructure features are defined once and tested here."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import duckdb
import numpy as np
import pandas as pd
import pytest

from financial_brain.evaluation import microstructure as MS
from financial_brain.ingest import archive_health as AH
from financial_brain.ingest import depth_archive as DA
from financial_brain.ingest import quote_archive as QA

IST = timezone(timedelta(hours=5, minutes=30))


class Clock:
    def __init__(self, at: str):
        self.t = datetime.fromisoformat(at).replace(tzinfo=IST)

    def now(self):
        return self.t

    def sleep(self, s: float):
        self.t += timedelta(seconds=s)


class FakeKite:
    class KiteNotConfigured(Exception):
        pass

    class KiteAuthError(Exception):
        pass

    def __init__(self, clock: Clock, *, login_at: str | None = "00:00", traded_today: bool = True, fail_quotes: int = 0,
                 always_fail: bool = False):
        self.clock, self.traded, self.fail, self.always_fail = clock, traded_today, fail_quotes, always_fail
        self.login_at = datetime.strptime(login_at, "%H:%M").time() if login_at else None
        self.calls = 0

    def credentials(self):
        if self.login_at is None or self.clock.now().time() < self.login_at:
            raise self.KiteNotConfigured("no token")
        return "key", "token"

    def instruments(self, exchange):
        rows = []
        for n in ("AAA", "BBB"):
            rows.append({"instrument_type": "FUT", "name": n, "expiry": "2026-11-26", "tradingsymbol": f"{n}26NOVFUT",
                         "strike": "0"})
        for k in (24000, 24050, 24100):
            for t in ("CE", "PE"):
                rows.append({"instrument_type": t, "name": "NIFTY", "expiry": "2026-11-13",
                             "tradingsymbol": f"NIFTY26N13{k}{t}", "strike": str(k)})
        return rows

    def quote(self, symbols, kind="ohlc"):
        self.calls += 1
        if self.always_fail or self.fail > 0:
            self.fail -= 1
            raise RuntimeError("503")
        now = self.clock.now()
        day = now.date().isoformat() if self.traded else (now.date() - timedelta(days=1)).isoformat()
        out = {}
        for i, s in enumerate(symbols):
            px = 100.0 + i + now.minute * 0.01
            out[s] = {"last_price": px, "average_price": px, "volume": 1000 * (now.hour * 60 + now.minute) + i,
                      "oi": 5000 + now.minute, "last_trade_time": f"{day} {now:%H:%M:%S}",
                      "ohlc": {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
                      "depth": {"buy": [{"price": px - 0.05, "quantity": 100 + j} for j in range(5)],
                                "sell": [{"price": px + 0.05, "quantity": 90 + j} for j in range(5)]}}
        return out


def _run(tmp_path, clock, kd, **kw):
    return DA.run(until=kw.pop("until", "09:40"), now_fn=clock.now, sleep=clock.sleep, kd=kd,
                  path=tmp_path / "depth.duckdb", **kw)


def test_full_session_collects_every_minute_and_logs_the_run(tmp_path):
    clock = Clock("2026-11-12 09:10")
    out = _run(tmp_path, clock, FakeKite(clock))
    assert out["status"] == "complete" and out["polls"] >= 24 and out["failed_polls"] == 0
    con = duckdb.connect(str(tmp_path / "depth.duckdb"), read_only=True)
    n_syms = con.execute("SELECT COUNT(DISTINCT symbol) FROM quotes").fetchone()[0]
    assert n_syms == 2 + 2 + 6                                    # two futures, two stocks, six option strikes
    assert con.execute("SELECT status FROM runs").fetchone()[0] == "complete"
    assert not (tmp_path / "depth.lock").exists()


def test_it_waits_for_a_late_login_and_then_collects(tmp_path):
    clock = Clock("2026-11-12 09:10")
    out = _run(tmp_path, clock, FakeKite(clock, login_at="09:20"))
    assert out["status"] == "complete" and out["polls"] > 0
    con = duckdb.connect(str(tmp_path / "depth.duckdb"), read_only=True)
    first = con.execute("SELECT MIN(ts) FROM quotes").fetchone()[0]
    assert first.hour == 9 and first.minute >= 20


def test_no_login_by_the_deadline_is_recorded_not_raised(tmp_path):
    clock = Clock("2026-11-12 09:10")
    out = _run(tmp_path, clock, FakeKite(clock, login_at=None), until="11:00", login_by="10:30")
    assert out["status"] == "no_login" and out["rows"] == 0
    con = duckdb.connect(str(tmp_path / "depth.duckdb"), read_only=True)
    assert con.execute("SELECT status FROM runs").fetchone()[0] == "no_login"


def test_a_holiday_is_detected_and_leaves_no_rows(tmp_path):
    clock = Clock("2026-11-12 09:10")
    out = _run(tmp_path, clock, FakeKite(clock, traded_today=False))
    assert out["status"] == "closed"
    con = duckdb.connect(str(tmp_path / "depth.duckdb"), read_only=True)
    assert con.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 0


def test_failed_polls_are_skipped_and_a_dead_feed_aborts(tmp_path, monkeypatch):
    clock = Clock("2026-11-12 09:10")
    out = _run(tmp_path, clock, FakeKite(clock, fail_quotes=3))     # one failure is spent on the index spot lookup
    assert out["status"] == "complete" and out["failed_polls"] == 2 and out["polls"] > 0
    monkeypatch.setattr(DA, "MAX_CONSECUTIVE_FAILURES", 4)
    clock2 = Clock("2026-11-13 09:10")
    out2 = DA.run(until="09:40", now_fn=clock2.now, sleep=clock2.sleep, kd=FakeKite(clock2, always_fail=True),
                  path=tmp_path / "dead.duckdb")
    assert out2["status"] == "aborted" and out2["failed_polls"] == 4


def test_a_second_copy_refuses_and_a_stale_lock_is_taken_over(tmp_path):
    lock = tmp_path / "depth.lock"
    lock.write_text("1 now")
    clock = Clock("2026-11-12 09:10")
    with pytest.raises(DA.AlreadyRunning):
        _run(tmp_path, clock, FakeKite(clock))
    old = datetime.now().timestamp() - DA.LOCK_STALE_S - 10
    import os
    os.utime(lock, (old, old))
    out = _run(tmp_path, Clock("2026-11-12 09:10"), FakeKite(clock))
    assert out["status"] in ("complete", "closed", "failed")
    assert not lock.exists()


# --------------------------------------------------------------------------------------------- health

def _fill(con, day: str, polls: int, names: int = 250, book: bool = True, start="09:15"):
    """Bulk-load synthetic snapshots (a DataFrame insert: row-by-row executemany of 80k rows is slow and fragile)."""
    ts = pd.date_range(f"{day} {start}", periods=polls, freq="min")
    sym = [f"S{i}FUT" if i < 60 else f"S{i}" for i in range(names)]
    df = pd.DataFrame({"ts": np.repeat(ts, names), "symbol": sym * polls})
    n = len(df)
    df["ltp"], df["vwap"] = 100.0, 100.0
    df["volume"] = np.repeat(np.arange(polls) * 1000.0, names)
    df["oi"] = np.where(df["symbol"].str.endswith("FUT"), 500.0, np.nan)
    df["open"], df["high"], df["low"], df["prev_close"] = 100.0, 101.0, 99.0, 100.0
    df["bid"], df["ask"] = (99.9, 100.1) if book else (np.nan, np.nan)
    df["bid_qty"], df["ask_qty"], df["depth_bid_qty"], df["depth_ask_qty"] = 10.0, 10.0, 50.0, 50.0
    assert len(df) == n
    con.register("fill_df", df)
    con.execute("INSERT INTO quotes SELECT ts, symbol, ltp, vwap, volume, oi, open, high, low, prev_close, bid, ask, "
                "bid_qty, ask_qty, depth_bid_qty, depth_ask_qty FROM fill_df")
    con.unregister("fill_df")


def test_coverage_tells_full_partial_empty_and_closed_apart(tmp_path):
    con = QA.connect(tmp_path / "a.duckdb")
    _fill(con, "2026-11-09", 340, names=210)                       # full day
    _fill(con, "2026-11-10", 90, names=210)                        # late login
    con.execute(DA.RUNS_SCHEMA)
    con.execute("INSERT INTO runs VALUES ('2026-11-12','x','y','closed',0,0,0,0,'holiday')")
    rows = AH.report(con, [date(2026, 11, 9), date(2026, 11, 10), date(2026, 11, 11), date(2026, 11, 12)])
    assert [r["status"] for r in rows] == ["OK", "PARTIAL", "EMPTY", "CLOSED"]
    assert rows[0]["futures_with_oi"] == 60 and rows[0]["depth_share"] == 1.0


def test_a_missing_book_is_not_a_full_day(tmp_path):
    con = QA.connect(tmp_path / "b.duckdb")
    _fill(con, "2026-11-09", 370, names=210, book=False)
    assert AH.coverage(con, "2026-11-09")["status"] == "PARTIAL"


def test_gaps_are_counted(tmp_path):
    con = QA.connect(tmp_path / "c.duckdb")
    _fill(con, "2026-11-09", 100, names=210)
    _fill(con, "2026-11-09", 100, names=210, start="11:30")
    c = AH.coverage(con, "2026-11-09")
    assert c["gaps"] == 1 and c["polls"] == 200


def test_alerts_fire_on_a_missed_session_not_on_a_holiday(tmp_path):
    ok = {"day": "2026-11-09", "status": "OK"}
    assert AH.alerts([ok, {"day": "2026-11-10", "status": "CLOSED"}], date(2026, 11, 10)) == []
    found = AH.alerts([ok, {"day": "2026-11-10", "status": "EMPTY", "polls": 0}], date(2026, 11, 10))
    assert len(found) == 1 and "2026-11-10" in found[0]["body"]
    again = AH.alerts([ok, {"day": "2026-11-10", "status": "EMPTY", "polls": 0}], date(2026, 11, 10))
    assert again[0]["key"] == found[0]["key"]                      # one alert per distinct failure per day


def test_readiness_counts_complete_sessions_toward_the_registration_gate():
    rows = [{"day": f"d{i}", "status": "OK"} for i in range(30)] + [{"day": "x", "status": "PARTIAL"}]
    r = AH.readiness(rows)
    assert r["ok_sessions"] == 30 and r["need_to_register"] == AH.MIN_SESSIONS_REGISTER - 30
    assert "not yet enough" in r["stage"]
    assert AH.readiness([{"day": str(i), "status": "OK"} for i in range(120)])["stage"].startswith("READY")


# --------------------------------------------------------------------------------------------- features

def _snap():
    ts = pd.date_range("2026-11-12 09:15", periods=4, freq="min")
    return pd.DataFrame({"ts": ts, "symbol": "X", "ltp": [100.0, 100.5, 100.5, 100.0], "volume": [1000.0, 1500, 1500, 2100],
                         "oi": [10.0, 12, 15, 11], "bid": [99.9, 100.4, 100.45, 99.95], "ask": [100.1, 100.6, 100.55, 100.05],
                         "bid_qty": [300.0, 100, 50, 90], "ask_qty": [100.0, 100, 150, 10],
                         "depth_bid_qty": [900.0, 400, 300, 500], "depth_ask_qty": [300.0, 400, 700, 100]})


def test_features_are_defined_as_documented():
    f = MS.features(_snap()).reset_index(drop=True)
    assert f.loc[0, "spread_bp"] == pytest.approx(0.2 / 100 * 1e4)
    assert f.loc[0, "imb_top"] == pytest.approx(0.5) and f.loc[1, "imb_top"] == pytest.approx(0.0)
    assert f.loc[2, "imb_top"] == pytest.approx(-0.5)
    assert f.loc[0, "imb_depth"] == pytest.approx(0.5)
    assert np.isnan(f.loc[0, "dvol"]) and f.loc[1, "dvol"] == 500 and f.loc[2, "dvol"] == 0
    assert f.loc[2, "doi"] == 3 and f.loc[3, "doi"] == -4
    assert f.loc[1, "signed_flow"] == 500 and f.loc[3, "signed_flow"] == -600 and f.loc[2, "signed_flow"] == 0


def test_features_never_use_a_later_snapshot():
    q = _snap()
    a = MS.features(q).reset_index(drop=True)
    cut = MS.features(q.iloc[:3]).reset_index(drop=True)
    for c in MS.COLUMNS:
        assert a.loc[:2, c].fillna(-9).tolist() == cut[c].fillna(-9).tolist(), c


def test_a_crossed_or_empty_book_gives_no_spread():
    q = _snap()
    q.loc[1, "bid"] = 101.0                                        # bid above ask
    q.loc[2, "ask"] = 0.0
    f = MS.features(q).reset_index(drop=True)
    assert np.isnan(f.loc[1, "spread_bp"]) and np.isnan(f.loc[2, "spread_bp"]) and np.isnan(f.loc[2, "imb_top"])


def test_spread_by_time_gives_median_by_bucket():
    f = MS.features(_snap())
    t = MS.spread_by_time(f, minutes=30)
    assert t.shape[0] == 1 and "09:00" in t.columns
