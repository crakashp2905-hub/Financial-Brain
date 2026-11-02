from datetime import datetime

from financial_brain.paper import live_view as V
from financial_brain.paper.intraday import DayEngine, Trade


def test_open_short_mark_is_gross_and_closed_net_is_separate():
    engine = DayEngine("2026-11-03")
    engine.trades = [
        Trade("a", engine.day, "ORB", "SHORT", "short", 10, "2026-11-03T09:40:00", 100, 102, 96),
        Trade("b", engine.day, "GAP", "CLOSED", "long", 10, "2026-11-03T09:40:00", 100, 98, 104,
              exit_ts="2026-11-03T10:00:00", exit_px=104, gross_pnl=40, costs=5, net_pnl=35),
    ]
    html = V.render(engine, prices={"SHORT": 97}, status="running")
    assert "₹30.00" in html and "₹35.00" in html
    assert "₹65.00" not in html  # never combine gross open P&L with net closed P&L
    assert "₹0.00" not in V.render()  # no feed must not imply zero profits


def test_snapshot_escapes_symbols_and_does_not_read_database(tmp_path):
    engine = DayEngine("2026-11-03")
    engine.trades = [Trade("a", engine.day, "ORB", "<script>bad</script>", "long", 1,
                           "2026-11-03T09:40:00", 100, 98, 104)]
    ledger = tmp_path / "missing.duckdb"
    V.publish(ledger, engine, now=datetime(2026, 11, 3, 10, tzinfo=V.IST))
    text = V.view_path(ledger).read_text(encoding="utf-8")
    assert "&lt;script&gt;bad&lt;/script&gt;" in text
    assert not ledger.exists()


def test_viewer_write_failure_is_nonfatal(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    V.publish(blocked / "ledger.duckdb", DayEngine("2026-11-03"))
