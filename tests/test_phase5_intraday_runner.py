from datetime import date, datetime, timedelta
import json
import tempfile
from pathlib import Path

import duckdb
import pytest

from financial_brain.paper import intraday_runner as R
from financial_brain.providers import kite_data as KD
from financial_brain.ingest import quote_archive as QA


class Clock:
    def __init__(self, start):
        self.t = start

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += timedelta(seconds=s)


def test_forward_archive_replay_reads_local_quotes_and_research_without_kite_calls(monkeypatch):
    day = date(2026, 11, 13)
    with tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".pe27-") as directory:
        root = Path(directory)
        quote_path, research_path, ledger_path = (root / "quotes.duckdb", root / "research.duckdb",
                                                  root / "paper.duckdb")
        quote_con = QA.connect(quote_path)
        for minute in range(360):
            ts = datetime(2026, 11, 13, 9, 15) + timedelta(minutes=minute)
            px = price_at(ts)
            QA.store(quote_con, ts, {"NSE:INFY": {"last_price": px, "volume": minute + 1,
                                                    "ohlc": {"open": 100.0, "high": px + .1,
                                                             "low": px - .1, "close": px}}})
        quote_con.close()
        with duckdb.connect(str(research_path)) as con:
            con.execute("CREATE TABLE adjusted_prices (ticker VARCHAR, business_date DATE, close_adj DOUBLE)")
            con.execute("INSERT INTO adjusted_prices VALUES ('INFY', '2026-11-12', 99.0)")
            con.execute("CREATE TABLE index_levels_canonical (index_name VARCHAR, variant VARCHAR, "
                        "business_date DATE, close_level DOUBLE)")
        monkeypatch.setattr(R, "universe", lambda n, *, research_path: [{"tradingsymbol": "INFY", "adv": 1e8}])
        monkeypatch.setattr(R, "REPORTS", root / "reports")
        monkeypatch.setattr(R, "daily_view", lambda **kwargs: (None, None, None))
        monkeypatch.setattr(R, "daily_up", lambda day, **kwargs: None)
        monkeypatch.setattr(KD, "candles", lambda *_args, **_kwargs: pytest.fail("network candle API called"))
        monkeypatch.setattr(KD, "instruments", lambda *_args, **_kwargs: pytest.fail("network instrument API called"))
        result = R.run_archive_replay(day, n=1, quote_archive_path=quote_path,
                                      research_path=research_path, ledger_path=ledger_path,
                                      cohort="local-fixture")
        assert result["day"] == day.isoformat()
        assert result["status"] == "complete" and result["symbols"] == 1
        assert result["data_source"] == "LOCAL_KITE_QUOTE_ARCHIVE"
        assert result["network_access"] is False and result["broker_orders"] is False
        actual_ledger = next(root.glob("paper-local-fixture-*.duckdb"))
        with duckdb.connect(str(actual_ledger), read_only=True) as con:
            recorded = con.execute("SELECT status, symbols, note FROM runs WHERE day=? AND mode='replay'",
                                    [day.isoformat()]).fetchone()
        assert recorded[0:2] == ("complete", 1)
        assert recorded[2].startswith("ARCHIVE_REPLAY_SHA256:")

        original_replay = R.run_replay
        monkeypatch.setattr(R, "run_replay", lambda *_args, **_kwargs: pytest.fail(
            "identical cohort/date must use verification-only path"))
        verified = R.run_archive_replay(day, n=1, quote_archive_path=quote_path,
                                        research_path=research_path, ledger_path=ledger_path,
                                        cohort="local-fixture")
        assert verified["status"] == "verified_existing" and verified["verification_only"] is True
        with QA.connect(quote_path) as con:
            con.execute("UPDATE quotes SET ltp=ltp + 0.01 WHERE symbol='INFY' AND CAST(ts AS DATE)=?", [day])
        with pytest.raises(ValueError, match="new cohort identifier"):
            R.run_archive_replay(day, n=1, quote_archive_path=quote_path,
                                 research_path=research_path, ledger_path=ledger_path,
                                 cohort="local-fixture")
        monkeypatch.setattr(R, "run_replay", original_replay)
        new_version = R.run_archive_replay(day, n=1, quote_archive_path=quote_path,
                                           research_path=research_path, ledger_path=ledger_path,
                                           cohort="local-fixture-v2")
        assert new_version["status"] == "complete" and new_version["verification_only"] is False


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
    from financial_brain.forward_edge import data_permissions
    monkeypatch.setattr(QA, "PATH", tmp_path / "quotes.duckdb")          # nor the real quote archive
    permission_registry = tmp_path / "data-permissions.json"
    permission_registry.write_text(json.dumps({
        "schema": "fb.data_permissions.v1",
        "permissions": [{
            "source_id": "KITE_CONNECT_READ_ONLY", "use": "PAPER_SIMULATION",
            "status": "USE_ALLOWED", "effective_from": "2026-01-01",
            "effective_through": None, "evidence_ref": "tests/synthetic-feed",
            "reviewer": "test-fixture",
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(data_permissions, "DEFAULT_PERMISSION_REGISTRY", permission_registry)
    run_live = R.run_live
    test_permission = {"source_id": "KITE_CONNECT_READ_ONLY", "use": "PAPER_SIMULATION",
                       "status": "USE_ALLOWED", "effective_from": "2026-01-01",
                       "effective_through": None, "evidence_ref": "tests/synthetic-feed",
                       "reviewer": "test-fixture"}

    def run_live_with_permission(*args, **kwargs):
        kwargs.setdefault("data_permission", test_permission)
        return run_live(*args, **kwargs)

    monkeypatch.setattr(R, "run_live", run_live_with_permission)
    return tmp_path


def test_live_kite_runner_blocks_missing_permission_before_quote_request(monkeypatch, tmp_path):
    clock = Clock(datetime(2026, 10, 6, 9, 10, tzinfo=R.IST))
    requested = []
    result = R.run_live(quote_fn=lambda *_args: requested.append(True), now_fn=clock.now,
                        path=tmp_path / "must-not-create.duckdb")
    assert result["status"] == "rights_blocked"
    assert result["quote_requests"] == 0 and requested == []
    assert result["broker_orders"] is False


def test_live_kite_runner_rejects_forged_permission_against_canonical_registry(monkeypatch, tmp_path):
    from financial_brain.forward_edge import data_permissions

    permission_registry = tmp_path / "data-permissions.json"
    permission_registry.write_text(json.dumps({
        "schema": "fb.data_permissions.v1",
        "permissions": [{
            "source_id": "KITE_CONNECT_READ_ONLY", "use": "PAPER_SIMULATION",
            "status": "UNKNOWN_RIGHTS", "effective_from": None,
            "effective_through": None, "evidence_ref": "external permission pending",
            "reviewer": None,
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(data_permissions, "DEFAULT_PERMISSION_REGISTRY", permission_registry)
    clock = Clock(datetime(2026, 10, 6, 9, 10, tzinfo=R.IST))
    requests = []
    forged = {"source_id": "KITE_CONNECT_READ_ONLY", "use": "PAPER_SIMULATION",
              "status": "USE_ALLOWED", "effective_from": "2026-01-01",
              "effective_through": None, "evidence_ref": "owner-created", "reviewer": "owner"}
    result = R.run_live(quote_fn=lambda *_args: requests.append(True), now_fn=clock.now,
                        data_permission=forged, path=tmp_path / "forged.duckdb")
    assert result["status"] == "rights_blocked"
    assert result["quote_requests"] == 0 and requests == []


def test_live_kite_runner_halts_before_quote_request_for_expired_registry_permission(monkeypatch, tmp_path):
    from financial_brain.forward_edge import data_permissions

    permission_registry = tmp_path / "data-permissions.json"
    expired = {"source_id": "KITE_CONNECT_READ_ONLY", "use": "PAPER_SIMULATION",
               "status": "USE_ALLOWED", "effective_from": "2026-01-01",
               "effective_through": "2026-10-05", "evidence_ref": "tests/expired",
               "reviewer": "test-fixture"}
    permission_registry.write_text(json.dumps({
        "schema": "fb.data_permissions.v1", "permissions": [expired],
    }), encoding="utf-8")
    monkeypatch.setattr(data_permissions, "DEFAULT_PERMISSION_REGISTRY", permission_registry)
    clock = Clock(datetime(2026, 10, 6, 9, 10, tzinfo=R.IST))
    requests = []
    result = R.run_live(quote_fn=lambda *_args: requests.append(True), now_fn=clock.now,
                        data_permission=expired, path=tmp_path / "expired.duckdb")
    assert result["status"] == "rights_blocked"
    assert result["quote_requests"] == 0 and requests == []


def test_a_live_day_opens_on_the_breakout_takes_the_target_and_writes_the_ledger(setup):
    c = Clock(datetime(2026, 10, 6, 9, 10, tzinfo=R.IST))
    out = R.run_live(quote_fn=fake_quotes(c), now_fn=c.now, sleep_fn=c.sleep, path=setup / "l.duckdb")
    assert out["status"] == "complete"
    con = R.ledger(setup / "l.duckdb")
    rows = con.execute("SELECT strategy, side, exit_reason, mode FROM trades").fetchall()
    assert ("ORB", "long", "target", "live") in rows
    note = json.loads(con.execute("SELECT note FROM runs WHERE day=? AND mode='live'",
                                  ["2026-10-06"]).fetchone()[0])
    assert note["data_permission"]["source_id"] == "KITE_CONNECT_READ_ONLY"
    assert note["data_permission"]["effective_from"] == "2026-01-01"
    assert len(note["data_permission"]["registry_sha256"]) == 64
    assert (setup / "reports" / "2026-10-06-live.md").exists()


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
    out = R.run_live(quote_fn=broken, now_fn=c.now, sleep_fn=c.sleep, path=setup / "l.duckdb",
                     max_failures=5, poll=30)
    assert out["status"] == "aborted" and "token expired" in out["why"]
    assert out["feed_health"]["status"] == "STALE"
    assert out["feed_health"]["decision_status"] == "NO_TRADE"
    assert out["feed_health"]["poll_attempts"] == out["feed_health"]["failed_polls"] == 5
    assert out["feed_health"]["dropped_packet_count"] is None
    note = R.ledger(setup / "l.duckdb").execute(
        "SELECT note FROM runs WHERE mode = 'live'").fetchone()[0]
    assert '"decision_status":"NO_TRADE"' in note
    assert '"failed_polls":5' in note


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
    assert out["feed_health"]["reconnects"] == 1
    assert out["feed_health"]["successful_polls"] == out["polls"]


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


def test_live_paper_and_replay_never_call_physical_broker_even_with_forged_flags(setup, monkeypatch):
    """Exercise both intraday simulation entry points under a forged live-order config."""
    import urllib.request

    from financial_brain.execution.oms.kite_broker import KiteBroker
    from financial_brain.forward_edge import modes

    calls = []
    monkeypatch.setattr(KiteBroker, "place", lambda *_args, **_kwargs: calls.append("place"))
    monkeypatch.setattr(KiteBroker, "_request",
                        lambda *_args, **_kwargs: pytest.fail("paper/replay called Kite transport"))
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *_args, **_kwargs: pytest.fail("paper/replay opened an HTTP transport"))
    monkeypatch.setattr(modes, "PHYSICAL_ORDERS_ENABLED", True)
    monkeypatch.setenv("FB_LIVE_ORDERS", "1")

    def candles(tok, start, end, interval):
        if interval == "day":
            return [{"ts": "2026-10-01T00:00:00+0530", "close": 100.0},
                    {"ts": "2026-10-05T00:00:00+0530", "close": 105.0}]
        return [{"ts": (datetime(2026, 10, 5, 9, 15) + timedelta(minutes=k)).strftime(
                    "%Y-%m-%dT%H:%M:%S+0530"), "open": price_at(datetime(2026, 10, 5, 9, 15) + timedelta(minutes=k)),
                 "high": price_at(datetime(2026, 10, 5, 9, 15) + timedelta(minutes=k)) + 0.05,
                 "low": price_at(datetime(2026, 10, 5, 9, 15) + timedelta(minutes=k)) - 0.05,
                 "close": price_at(datetime(2026, 10, 5, 9, 15) + timedelta(minutes=k)), "volume": 1000}
                for k in range(360)]

    clock = Clock(datetime(2026, 10, 6, 9, 10, tzinfo=R.IST))
    live = R.run_live(poll=3600, quote_fn=fake_quotes(clock), now_fn=clock.now, sleep_fn=clock.sleep,
                      candles_fn=candles, token_fn=lambda _symbol: 1,
                      path=setup / "forged-live-paper.duckdb")
    assert live["status"] == "complete"
    assert R.ledger(setup / "forged-live-paper.duckdb").execute(
        "SELECT COUNT(*) FROM trades").fetchone()[0] > 0

    replay = R.run_replay(date(2026, 10, 5), candles_fn=candles, token_fn=lambda _symbol: 1,
                          path=setup / "forged-replay.duckdb")
    assert replay["summary"]["ORB"]["trades"] > 0
    assert calls == []


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
            v["timestamp"] = c.now().isoformat()
            v["depth"] = {"buy": [{"price": v["last_price"] - 0.05, "quantity": 10}],
                          "sell": [{"price": v["last_price"] + 0.05, "quantity": 12}]}
        return out
    out = R.run_live(poll=900, quote_fn=q, now_fn=c.now, sleep_fn=c.sleep, path=setup / "l.duckdb")
    assert set(kinds) == {"full"}
    assert out["forward_quote_archive"]["archived"] > 0
    from financial_brain.forward_edge.quote_archive import QuoteArchive
    archive = QuoteArchive(setup / "l-forward-events.sqlite")
    event = archive.get(archive._con.execute("SELECT event_id FROM market_events LIMIT 1").fetchone()[0])
    archive.close()
    assert event.source == "KITE_CONNECT_READ_ONLY" and event.venue == "NSE"
    assert event.received_at == event.available_at
    assert event.event_time <= event.received_at
    row = R.ledger(setup / "l.duckdb").execute(
        "SELECT entry_px, entry_decision_px, spread_bp, shortfall_bp FROM trades").fetchone()
    assert row[0] == pytest.approx(row[1] + 0.05) and row[2] > 0 and row[3] > 0


def _feed_breaks_after_entry(c, recover_at=None):
    good = fake_quotes(c)

    def q(symbols, kind):
        m = c.t.hour * 60 + c.t.minute
        if m >= 592 and (recover_at is None or m < recover_at):      # 09:52: after the ORB entry
            raise OSError("getaddrinfo failed")
        if m < 592:
            return good(symbols, kind)
        return {s: {"last_price": 106.9, "last_trade_time": "2026-10-06 11:00:00",
                    "ohlc": {"open": 100.0, "high": 106.9, "low": 99.0, "close": 100.0}} for s in symbols}
    return q


def test_feed_failure_with_open_positions_closes_them_at_the_flatten_not_abandons_them(setup):
    c = Clock(datetime(2026, 10, 6, 9, 10, tzinfo=R.IST))
    out = R.run_live(quote_fn=_feed_breaks_after_entry(c), now_fn=c.now, sleep_fn=c.sleep,
                     path=setup / "l.duckdb", max_failures=5)
    assert out["status"] == "aborted"
    rows = R.ledger(setup / "l.duckdb").execute("SELECT exit_reason, exit_px, exit_ts FROM trades").fetchall()
    assert rows and all(r[0] == "time_stale_quote" and abs(r[1] - 101.6) < 0.1 for r in rows)
    assert all(r[2] >= "2026-10-06T15:15" for r in rows)


def test_feed_failure_that_recovers_keeps_the_rules_running(setup):
    c = Clock(datetime(2026, 10, 6, 9, 10, tzinfo=R.IST))
    out = R.run_live(quote_fn=_feed_breaks_after_entry(c, recover_at=660), now_fn=c.now, sleep_fn=c.sleep,
                     path=setup / "l.duckdb", max_failures=5)
    assert out["status"] == "complete"
    rows = R.ledger(setup / "l.duckdb").execute("SELECT exit_reason FROM trades").fetchall()
    assert ("target",) in rows
