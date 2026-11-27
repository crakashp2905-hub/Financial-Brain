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


def test_reports_dated_index_and_option_family_coverage_without_claiming_liquidity(tmp_path):
    fo, quotes, index = tmp_path / "fo.duckdb", tmp_path / "quotes.duckdb", tmp_path / "index.duckdb"
    with duckdb.connect(str(fo)) as con:
        con.execute("""CREATE TABLE fo_bhav (trade_date DATE, instrument VARCHAR, symbol VARCHAR,
                    expiry DATE, strike DOUBLE, option_type VARCHAR, contracts DOUBLE, oi DOUBLE)""")
        con.execute("""INSERT INTO fo_bhav VALUES
                    ('2026-10-29','OPTIDX','NIFTY','2026-11-05',25000,'CE',100,800),
                    ('2026-10-29','OPTIDX','NIFTY','2026-11-05',25000,'PE',0,0),
                    ('2026-10-29','OPTIDX','FINNIFTY','2026-11-05',24000,'CE',0,0)""")
    with duckdb.connect(str(index)) as con:
        con.execute("CREATE TABLE index_levels (business_date DATE, index_name VARCHAR, variant VARCHAR)")
        con.execute("INSERT INTO index_levels VALUES ('2026-10-29','Nifty 50','PRICE')")
    result = report(fo, quotes, index)
    assert result["index_daily"]["families"] == [
        {"index_name": "Nifty 50", "first": "2026-10-29", "last": "2026-10-29", "rows": 1, "sessions": 1}]
    assert [item["symbol"] for item in result["optionable_families_by_date"]] == ["FINNIFTY", "NIFTY"]
    assert result["optionable_families_by_date"][0]["liquidity_evidence"] == "CONTRACTS_EXIST_LIQUIDITY_UNPROVEN"
    assert result["optionable_families_by_date"][1]["liquidity_evidence"] == "VOLUME_AND_OI_PRESENT"
