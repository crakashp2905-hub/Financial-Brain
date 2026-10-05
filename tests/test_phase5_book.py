"""The suggestion paper book (book-v1): entry, stop and time exits, slots, and a record that keeps the losers."""
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from financial_brain.paper import book as B

D0 = date(2026, 1, 1)


def path(prices):
    return pd.Series(prices, index=[D0 + timedelta(days=i + 1) for i in range(len(prices))])


def test_enters_at_the_first_close_and_sells_at_the_horizon():
    w = B.walk(path([100, 101, 102, 103, 104]), stop=90, horizon=3)
    assert w["entry_px"] == 100 and w["exit_px"] == 103 and w["exit_reason"] == "time" and w["held"] == 3


def test_the_first_close_at_or_below_the_stop_sells():
    w = B.walk(path([100, 95, 90, 120, 130]), stop=90, horizon=4)
    assert w["exit_px"] == 90 and w["exit_reason"] == "stop" and w["exit_day"] == D0 + timedelta(days=3)


def test_an_unfinished_position_stays_open_and_is_marked():
    w = B.walk(path([100, 104]), stop=90, horizon=60)
    assert w["exit_day"] is None and w["last_px"] == 104 and w["held"] == 1


def test_no_data_yet_means_waiting_to_buy():
    assert B.walk(pd.Series(dtype=float), stop=90, horizon=5)["entry_day"] is None


def test_slots_and_held_names_limit_what_is_taken():
    ranked = pd.DataFrame({"ticker": list("ABCDEFG")})
    got = B.choose(ranked, {"B"}, open_count=8)
    assert list(got["ticker"]) == ["A", "C"]                  # top five minus B, two free slots
    assert list(got["rank"]) == [1, 3]
    assert B.choose(ranked, set(), open_count=B.MAX_OPEN).empty


def _book(tmp_path: Path, rows):
    con = B.connect(tmp_path / "b.duckdb")
    for r in rows:
        con.execute("""INSERT INTO positions (version, mode, signal_day, rank, ticker, lineage, type, horizon, shares,
                       stop, ref_price, expected_net, cost, entry_day, entry_px, exit_day, exit_px, exit_reason, last_day,
                       last_px, held, bench) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", r)
    return con


def row(mode, i, exit_px, bench=0.0, sold=True):
    d = D0 + timedelta(days=i)
    return [B.VERSION, mode, d, 1, f"T{i}", f"L{i}", "momentum", 60, 10, 80.0, 100.0, 0.003, 0.003,
            d + timedelta(days=1), 100.0, (d + timedelta(days=60)) if sold else None, exit_px if sold else None,
            "time" if sold else None, d + timedelta(days=60), exit_px, 40, bench]


def test_the_record_keeps_losers_and_nets_costs_and_the_universe(tmp_path):
    con = _book(tmp_path, [row("replay", 1, 110, 0.02), row("replay", 2, 95, 0.0), row("replay", 3, 101, 0, sold=False)])
    df = B.frame(con, "replay")
    assert set(df["status"]) == {"SOLD", "OPEN"}
    s = B.summary(con, "replay")
    assert s["closed"] == 2 and s["open"] == 1
    assert s["win_rate"] == 0.5                                  # the loser is counted, not hidden
    assert s["mean_net"] == pytest.approx(((0.10 - 0.003) + (-0.05 - 0.003)) / 2)
    assert s["mean_excess"] == pytest.approx(((0.097 - 0.02) + (-0.053)) / 2)
    assert s["verdict"].startswith("REPLAY")


def test_live_needs_a_hundred_trades_over_twenty_days_before_a_verdict(tmp_path):
    con = _book(tmp_path, [row("live", i, 120) for i in range(1, 30)])
    assert B.summary(con, "live")["verdict"].startswith("COLLECTING")


def test_the_book_has_no_order_code():
    src = Path(B.__file__).read_text(encoding="utf-8")
    for bad in ("place_order", "/orders", "urllib", "requests"):
        assert bad not in src


def test_the_terminal_panel_shows_the_worst_trades_beside_the_best(tmp_path):
    from financial_brain.paper import terminal as T
    con = _book(tmp_path, [row("replay", 1, 130), row("replay", 2, 70), row("replay", 3, 101, sold=False)])
    html = T.book_panel(con)
    assert "Worst five sold" in html and "Best five sold" in html and "T2" in html      # the loser is on the page
    assert "Holding now" in html and "T3" in html and "demonstration" in html


def test_the_swing_book_is_kept_apart_and_a_week_replay_is_labelled(tmp_path):
    from financial_brain.paper import terminal as T
    con = _book(tmp_path, [row("replay", 1, 130)])
    r = row("replay-week", 2, 90, sold=False)
    r[0] = B.SWING.version
    con.execute("""INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", r)
    assert B.summary(con, "replay-week", B.SWING)["open"] == 1
    assert B.summary(con, "replay", B.SWING)["closed"] == 0           # the position book's trade is not the swing book's
    html = T.book_panel(con, B.SWING)
    assert "one week decides nothing" in html and "T2" in html and "Open P&L, net" in html
    assert B.modes(con, B.SWING) == ["live", "replay-week"]
    with pytest.raises(B.BookError):
        B.replay(None, con, D0, D0, spec=B.SWING, mode="live")
