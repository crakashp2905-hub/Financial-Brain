import duckdb

from financial_brain.ingest.options_audit import report


def test_missing_sources_are_not_backtest_ready(tmp_path):
    result = report(tmp_path / "fo.duckdb", tmp_path / "quotes.duckdb")
    assert result["fo_daily"]["status"] == "UNAVAILABLE"
    assert result["historical_bid_ask_backtest_ready"] is False


def test_daily_and_quote_coverage_are_separate(tmp_path):
    fo, quotes = tmp_path / "fo.duckdb", tmp_path / "quotes.duckdb"
    with duckdb.connect(str(fo)) as con:
        con.execute("CREATE TABLE fo_bhav (trade_date DATE, instrument VARCHAR, option_type VARCHAR)")
        con.execute("INSERT INTO fo_bhav VALUES ('2026-10-29','OPTIDX','CE'), ('2026-10-29','OPTIDX','PE')")
    with duckdb.connect(str(quotes)) as con:
        con.execute("CREATE TABLE option_quotes (received_at TIMESTAMPTZ, book_quality VARCHAR)")
        con.execute("INSERT INTO option_quotes VALUES ('2026-10-29T10:00:00Z','TWO_SIDED'),"
                    "('2026-10-29T10:01:00Z','INCOMPLETE_OR_CROSSED')")
    result = report(fo, quotes)
    assert result["fo_daily"]["calls"] == 1
    assert result["fo_daily"]["puts"] == 1
    assert result["live_quotes"]["two_sided_fraction"] == .5
    assert result["historical_bid_ask_backtest_ready"] is False
