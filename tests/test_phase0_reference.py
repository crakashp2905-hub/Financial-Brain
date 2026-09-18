"""Tests for the Phase 0 gap-closers: reference data, benchmarks, derived corporate
actions, and the point-in-time history CLI path.

Offline, like the rest of the suite - parsers are exercised against captured payload
shapes rather than the network.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.config import Config
from financial_brain.providers.base import FetchResult
from financial_brain.corpactions import detect
from financial_brain.providers.reference import (NSEEquityListProvider, NSEFnoLotsProvider,
                                                 NSEIndexCloseProvider)
from financial_brain.storage.db import Database


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(data_root=tmp_path).ensure()


@pytest.fixture
def db(cfg) -> Database:
    d = Database(cfg)
    d.migrate()
    return d


# ------------------------------------------------------------------- parsers
INDEX_CSV = (
    "Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,"
    "Closing Index Value,Points Change,Change(%),Volume,Turnover (Rs. Cr.),P/E,P/B,Div Yield\n"
    "Nifty 50,11-09-2026,23270.3,23448.1,23231.4,23398.1,-79.7,-.34,293025966,20905.97,19.78,2.83,1.21\n"
    "Nifty Next 50,11-09-2026,71926.55,72238.45,71400.25,72083.7,-447.2,-.62,163352409,8626.67,19.06,3.21,1\n"
)

EQUITY_L_CSV = (
    "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT,"
    " ISIN NUMBER, FACE VALUE\n"
    "20MICRONS,20 Microns Limited,EQ,06-OCT-2008,5,1,INE144J01027,5\n"
    "TCS,Tata Consultancy Services Limited,EQ,25-AUG-2004,1,1,INE467B01029,1\n"
)

FO_CSV = (
    "UNDERLYING                          ,SYMBOL    ,SEP-26     ,OCT-26     ,\n"
    "NIFTY FINANCIAL SERVICES            ,FINNIFTY  ,60         ,60         ,\n"
    "Tata Consultancy Services Limited   ,TCS       ,175        ,175        ,\n"
)


class TestReferenceParsers:
    def test_index_close_parses_levels(self):
        rows = NSEIndexCloseProvider.parse(INDEX_CSV.encode(), date(2026, 9, 11))
        assert len(rows) == 2
        nifty = rows[0]
        assert nifty["index_name"] == "Nifty 50"
        assert nifty["close_level"] == pytest.approx(23398.1)
        assert nifty["pe"] == pytest.approx(19.78)

    def test_equity_list_parses_listing_dates(self):
        """Listing date is the valuable column - it bounds universe membership."""
        rows = NSEEquityListProvider.parse(EQUITY_L_CSV.encode())
        by_isin = {r["isin"]: r for r in rows}
        tcs = by_isin["INE467B01029"]
        assert tcs["symbol"] == "TCS"
        assert tcs["listing_date"] == date(2004, 8, 25)
        assert tcs["face_value"] == pytest.approx(1.0)

    def test_fo_lots_parses_symbols_and_lots(self):
        rows = NSEFnoLotsProvider.parse(FO_CSV.encode())
        by_sym = {r["symbol"]: r for r in rows}
        assert by_sym["TCS"]["lot_size"] == 175
        assert "FINNIFTY" in by_sym          # index futures have no ISIN; that is fine


# ------------------------------------------- derived corporate actions
class TestActionClassification:
    def test_one_for_five_split(self):
        t, f, to = detect.classify(0.2)
        assert (t, f, to) == ("SPLIT", 1.0, 5.0)

    def test_halving_is_recorded_with_its_ratio(self):
        """1:1 bonus and 1:2 split both halve the price - the ambiguity is documented."""
        t, f, to = detect.classify(0.5)
        assert t == "SPLIT" and f / to == pytest.approx(0.5)

    def test_unclassifiable_factor_still_yields_an_adjustment(self):
        """Rights and ex-dividend produce arbitrary factors. The factor is what matters."""
        t, f, to = detect.classify(0.8383)
        assert t == "ADJUSTMENT" and f is None and to is None

    def test_negative_or_zero_is_not_a_ratio(self):
        assert detect.classify(0.0)[0] == "ADJUSTMENT"


def _price_row(con, d, isin, exch, series, close, prev, ticker="T"):
    con.execute(
        """INSERT INTO universe_snapshots
           (business_date, isin, exchange, ticker, series, instrument_type,
            close_price, turnover, tradable) VALUES (?,?,?,?,?,?,?,?,?)""",
        [d, isin, exch, ticker, series, "STK", close, 5_000_000.0, True])


def _make_eod_view(con, rows):
    """Stand in for the curated Parquet view with an in-memory table."""
    con.execute("""CREATE OR REPLACE TEMP TABLE _eod (
        business_date DATE, exchange VARCHAR, isin VARCHAR, ticker VARCHAR,
        series VARCHAR, instrument_type VARCHAR, instrument_id VARCHAR,
        close_price DOUBLE, prev_close DOUBLE)""")
    for r in rows:
        con.execute("INSERT INTO _eod VALUES (?,?,?,?,?,'STK','1',?,?)", list(r))
    con.execute("CREATE OR REPLACE TEMP VIEW eod_prices AS SELECT * FROM _eod")


class TestDerivedActions:
    def test_detects_a_split_from_the_restated_previous_close(self, cfg, db):
        """The core mechanism: the exchange itself restates prev_close on the ex-date."""
        with db.connect() as con:
            _make_eod_view(con, [
                (date(2026, 9, 10), "NSE", "INE000000019", "T", "EQ", 500.0, 498.0),
                (date(2026, 9, 11), "NSE", "INE000000019", "T", "EQ", 101.0, 100.0),
            ])
            cands = detect.find_candidates(con)
        assert len(cands) == 1
        assert cands[0]["factor"] == pytest.approx(0.2)
        assert detect.classify(cands[0]["factor"])[0] == "SPLIT"

    def test_cross_exchange_agreement_is_corroboration(self, cfg, db):
        with db.connect() as con:
            _make_eod_view(con, [
                (date(2026, 9, 10), "NSE", "INE000000019", "T", "EQ", 500.0, 498.0),
                (date(2026, 9, 11), "NSE", "INE000000019", "T", "EQ", 101.0, 100.0),
                (date(2026, 9, 10), "BSE", "INE000000019", "T", "A", 500.5, 498.0),
                (date(2026, 9, 11), "BSE", "INE000000019", "T", "A", 101.0, 100.1),
            ])
            graded = detect.corroborate(detect.find_candidates(con))
        assert len(graded) == 1
        assert graded[0]["confidence"] == "corroborated"
        assert graded[0]["exchanges"] == "BSE+NSE"

    def test_one_exchange_only_is_graded_lower(self, cfg, db):
        with db.connect() as con:
            _make_eod_view(con, [
                (date(2026, 9, 10), "BSE", "INE000000027", "Q", "XT", 100.0, 99.0),
                (date(2026, 9, 11), "BSE", "INE000000027", "Q", "XT", 140.0, 130.0),
            ])
            graded = detect.corroborate(detect.find_candidates(con))
        assert graded[0]["confidence"] == "single_exchange"

    def test_series_contamination_does_not_create_false_actions(self, cfg, db):
        """Regression for TCS 2026-06-24.

        A block-deal (BL) row carried prev_close 3019.00 while the EQ row was a clean
        2059.60. Comparing across series invented a 1.47x 'corporate action' for TCS.
        Matching on series removes it.
        """
        with db.connect() as con:
            _make_eod_view(con, [
                (date(2026, 6, 23), "NSE", "INE467B01029", "TCS", "EQ", 2059.6, 2127.8),
                (date(2026, 6, 24), "NSE", "INE467B01029", "TCS", "EQ", 2109.0, 2059.6),
                (date(2026, 6, 24), "NSE", "INE467B01029", "TCS", "BL", 2059.6, 3019.0),
            ])
            cands = detect.find_candidates(con)
        assert cands == [], "the BL series must not be compared against EQ"

    def test_derived_factor_beats_classification_when_rebuilding(self, cfg, db):
        """An observed factor needs no classification to be correct."""
        with db.connect() as con:
            con.execute(
                """INSERT INTO corporate_actions
                   (action_id, isin, action_type, ex_date, source, source_tier,
                    observed_at, confidence, derived_factor)
                   VALUES ('a1','INE000000019','ADJUSTMENT', DATE '2026-09-11',
                           'NSE', 1, now(), 'corroborated', 0.8383)""")
            n = detect.rebuild_derived_factors(con)
            factor = con.execute(
                "SELECT price_factor FROM adjustment_factors WHERE isin='INE000000019'"
            ).fetchone()[0]
        assert n == 1
        assert factor == pytest.approx(0.8383), "unclassified actions still adjust prices"

    def test_factors_compound_across_multiple_actions(self, cfg, db):
        with db.connect() as con:
            for aid, ex, f in [("a1", date(2026, 7, 1), 0.5), ("a2", date(2026, 9, 1), 0.2)]:
                con.execute(
                    """INSERT INTO corporate_actions
                       (action_id, isin, action_type, ex_date, source, source_tier,
                        observed_at, derived_factor)
                       VALUES (?,?,'SPLIT',?,'NSE',1,now(),?)""",
                    [aid, "INE000000019", ex, f])
            detect.rebuild_derived_factors(con)
            rows = dict(con.execute(
                "SELECT effective_from, price_factor FROM adjustment_factors "
                "WHERE isin='INE000000019'").fetchall())
        # A price before both actions is adjusted by the product of the two.
        assert rows[date(2026, 7, 1)] == pytest.approx(0.1)
        assert rows[date(2026, 9, 1)] == pytest.approx(0.2)


# ------------------------------------------------------------- PIT CLI path
class TestPITHistoryCLI:
    def test_history_lookup_returns_the_restatement_trail(self, cfg, db, monkeypatch):
        """Regression: the CLI passed `con and entity` instead of the entity key."""
        from financial_brain.pit.observations import Observation, PITStore
        with db.connect() as con:
            store = PITStore(con)
            first = store.record(Observation(
                entity_key="INE467B01029", attribute="revenue", value_num=100.0,
                period_end=date(2026, 6, 30), source="FILING",
                published_at=datetime(2026, 8, 15, tzinfo=timezone.utc)))
            store.revise(first, Observation(
                entity_key="INE467B01029", attribute="revenue", value_num=92.0,
                period_end=date(2026, 6, 30), source="FILING",
                published_at=datetime(2026, 11, 1, tzinfo=timezone.utc)))
            trail = store.history("INE467B01029", "revenue")
        assert [t["value_num"] for t in trail] == [100.0, 92.0]


# --------------------------------------------- close-to-close gap detector
class TestGapDetector:
    """NSE does not restate prev_close for ordinary splits.

    Verified against ZFCVINDIA (2026-06-24), GOODLUCK (2026-08-21) and PGIL
    (2026-09-11): the stated previous close was the raw unadjusted one, so the
    restated-prev detector is blind to all of them and a second detector is required.
    """

    def test_restated_detector_is_blind_to_a_plain_gap(self, cfg, db):
        with db.connect() as con:
            _make_eod_view(con, [
                (date(2026, 8, 20), "NSE", "INE000000019", "GOODLUCK", "EQ", 1439.40, 1363.30),
                (date(2026, 8, 21), "NSE", "INE000000019", "GOODLUCK", "EQ", 490.90, 1439.40),
            ])
            assert detect.find_candidates(con) == []

    def test_gap_detector_finds_it(self, cfg, db):
        with db.connect() as con:
            _make_eod_view_with_turnover(con, [
                (date(2026, 8, 20), "NSE", "INE000000019", "GOODLUCK", "EQ", 1439.40, 5e7),
                (date(2026, 8, 21), "NSE", "INE000000019", "GOODLUCK", "EQ", 490.90, 5e7),
            ])
            cands = detect.find_gap_candidates(con)
        assert len(cands) == 1
        assert cands[0]["factor"] == pytest.approx(490.90 / 1439.40, rel=1e-6)
        assert detect.classify(cands[0]["factor"], tolerance=0.05)[0] == "SPLIT"

    def test_rights_entitlements_are_excluded(self, cfg, db):
        """-RE instruments are volatile by construction, not by corporate action."""
        with db.connect() as con:
            _make_eod_view_with_turnover(con, [
                (date(2026, 6, 18), "NSE", "INE000000027", "RELTD-RE", "EQ", 25.98, 5e7),
                (date(2026, 6, 19), "NSE", "INE000000027", "RELTD-RE", "EQ", 36.37, 5e7),
            ])
            assert detect.find_gap_candidates(con) == []

    def test_illiquid_names_are_excluded(self, cfg, db):
        with db.connect() as con:
            _make_eod_view_with_turnover(con, [
                (date(2026, 8, 20), "BSE", "INE000000035", "THIN", "XT", 100.0, 1_000.0),
                (date(2026, 8, 21), "BSE", "INE000000035", "THIN", "XT", 40.0, 1_000.0),
            ])
            assert detect.find_gap_candidates(con) == []

    def test_single_exchange_gap_needs_a_tight_snap(self, cfg, db):
        """PGIL snapped to 1:2 at 0.13% and is recorded; ESCONET's +87% is not.

        With one exchange there is no corroboration, so the ratio must carry the
        evidence by itself.
        """
        with db.connect() as con:
            _make_eod_view_with_turnover(con, [
                (date(2026, 9, 10), "NSE", "INE_PGIL00019", "PGIL", "EQ", 2378.40, 5e7),
                (date(2026, 9, 11), "NSE", "INE_PGIL00019", "PGIL", "EQ", 1187.70, 5e7),
                (date(2026, 9, 5), "NSE", "INE_ESCO00019", "ESCONET", "EQ", 133.10, 5e7),
                (date(2026, 9, 7), "NSE", "INE_ESCO00019", "ESCONET", "EQ", 249.40, 5e7),
            ])
            out = detect.derive_from_gaps(con)
            recorded = {r[0] for r in con.execute(
                "SELECT isin FROM corporate_actions").fetchall()}
        assert "INE_PGIL00019" in recorded, "a 0.13% snap to 1:2 is a split"
        assert "INE_ESCO00019" not in recorded, "an 87% rally is not a corporate action"
        assert out["written"] == 1

    def test_snapped_ratio_is_used_not_the_noisy_observed_gap(self, cfg, db):
        """The observed gap includes the day's real price move; the ratio does not."""
        with db.connect() as con:
            _make_eod_view_with_turnover(con, [
                (date(2026, 6, 23), "NSE", "INE_ZF0000019", "ZFCV", "EQ", 16086.0, 5e7),
                (date(2026, 6, 24), "NSE", "INE_ZF0000019", "ZFCV", "EQ", 2660.0, 5e7),
                (date(2026, 6, 23), "BSE", "INE_ZF0000019", "ZFCV", "A", 16108.95, 5e7),
                (date(2026, 6, 24), "BSE", "INE_ZF0000019", "ZFCV", "A", 2659.85, 5e7),
            ])
            detect.derive_from_gaps(con)
            row = con.execute(
                """SELECT ratio_from, ratio_to, derived_factor, confidence
                   FROM corporate_actions WHERE isin = 'INE_ZF0000019'""").fetchone()
        assert (row[0], row[1]) == (1.0, 6.0)
        assert row[2] == pytest.approx(1 / 6), "exact ratio, not the ~0.1653 observed gap"
        assert row[3] == "corroborated_gap"


def _make_eod_view_with_turnover(con, rows):
    con.execute("""CREATE OR REPLACE TEMP TABLE _eod2 (
        business_date DATE, exchange VARCHAR, isin VARCHAR, ticker VARCHAR,
        series VARCHAR, instrument_type VARCHAR, instrument_id VARCHAR,
        open_price DOUBLE, close_price DOUBLE, prev_close DOUBLE, turnover DOUBLE)""")
    # open = close: a real action is applied before the open, so the stock *opens* at
    # the adjusted price. Tests of intraday moves pass their own open.
    for row in rows:
        d, exch, isin, tk, ser, close, turnover, *rest = row
        opn = rest[0] if rest else close
        con.execute("INSERT INTO _eod2 VALUES (?,?,?,?,?,'STK','1',?,?,NULL,?)",
                    [d, exch, isin, tk, ser, opn, close, turnover])
    con.execute("CREATE OR REPLACE TEMP VIEW eod_prices AS SELECT * FROM _eod2")


class TestPlausibleRatios:
    def test_absurd_ratios_are_not_plausible(self):
        """A generic closest-fraction search called 0.8383 a '16:19 split'."""
        assert (16, 19) not in detect.PLAUSIBLE_RATIOS
        assert (12, 7) not in detect.PLAUSIBLE_RATIOS

    def test_real_split_and_bonus_ratios_are_plausible(self):
        for ratio in [(1, 2), (1, 5), (1, 10), (2, 3), (3, 2), (1, 6), (1, 3)]:
            assert ratio in detect.PLAUSIBLE_RATIOS


# ------------------------------------------------- legacy format normalisation
NSE_LEGACY_CSV = (
    "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,"
    "TIMESTAMP,TOTALTRADES,ISIN,\n"
    "20MICRONS,EQ,36,36.8,35.25,35.8,35.65,35.2,49077,1764577.1,01-JAN-2020,370,"
    "INE144J01027,\n"
    "TCS,EQ,2150,2180,2140,2175,2174,2145,100000,217500000,01-JAN-2020,5000,"
    "INE467B01029,\n"
)

BSE_LEGACY_CSV = (
    "SC_CODE,SC_NAME,SC_GROUP,SC_TYPE,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,NO_TRADES,"
    "NO_OF_SHRS,NET_TURNOV,TDCLOINDI,ISIN_CODE,TRADING_DATE,FILLER2,FILLER3\n"
    "500002,ABB LTD.    ,A ,Q,2700.00,2700.00,2673.25,2688.30,2687.65,2681.00,751,"
    "4344,11669853.00,,INE117A01022,03-Jan-23,,\n"
)


def _rows(csv_bytes):
    import csv as _csv, io as _io
    return list(_csv.DictReader(_io.StringIO(csv_bytes.decode())))


class TestLegacyNormalisation:
    """Both exchanges moved to UDiFF in July 2024; before that each had its own format.

    The legacy formats are normalised *into* UDiFF so the ingest job, quality contracts,
    security master and universe code are identical for a 2015 file and a 2026 one.
    """

    def test_nse_legacy_becomes_udiff(self):
        from financial_brain.providers.bhavcopy import normalise
        rows = _rows(normalise(NSE_LEGACY_CSV.encode()))
        assert len(rows) == 2
        tcs = [r for r in rows if r["TckrSymb"] == "TCS"][0]
        assert tcs["TradDt"] == "2020-01-01", "legacy '01-JAN-2020' must become ISO"
        assert tcs["ISIN"] == "INE467B01029"
        assert tcs["ClsPric"] == "2175"
        assert tcs["FinInstrmTp"] == "STK"
        assert tcs["TtlTradgVol"] == "100000"

    def test_bse_legacy_becomes_udiff(self):
        from financial_brain.providers.bhavcopy import normalise
        rows = _rows(normalise(BSE_LEGACY_CSV.encode()))
        assert len(rows) == 1
        abb = rows[0]
        assert abb["TradDt"] == "2023-01-03", "legacy '03-Jan-23' must become ISO"
        assert abb["ISIN"] == "INE117A01022"
        assert abb["FinInstrmId"] == "500002", "BSE scrip code is kept"
        assert abb["SctySrs"] == "A"
        assert abb["TtlTradgVol"] == "4344"

    def test_udiff_passes_through_untouched(self):
        from financial_brain.providers.bhavcopy import normalise
        payload = (HDR_UDIFF + "\n").encode()
        assert normalise(payload) == payload

    def test_format_is_sniffed_not_assumed_from_date(self):
        """A mislabelled file must not be parsed with the wrong reader."""
        from financial_brain.providers.bhavcopy import normalise
        rows = _rows(normalise(BSE_LEGACY_CSV.encode()))
        assert rows[0]["Src"] == "BSE", "sniffed as BSE legacy regardless of any date"

    def test_unknown_format_is_rejected_loudly(self):
        from financial_brain.providers.base import FetchError
        from financial_brain.providers.bhavcopy import normalise
        with pytest.raises(FetchError, match="unrecognised bhavcopy format"):
            normalise(b"col1,col2\n1,2\n")

    def test_rows_without_isin_are_dropped(self):
        from financial_brain.providers.bhavcopy import normalise
        csv_text = NSE_LEGACY_CSV + (
            "NOISIN,EQ,1,1,1,1,1,1,1,1,01-JAN-2020,1,,\n")
        assert len(_rows(normalise(csv_text.encode()))) == 2, "ISIN is the key; no ISIN, no row"

    def test_legacy_output_satisfies_the_quality_contracts(self, cfg, db):
        """The point of normalising: one set of contracts covers every era."""
        from financial_brain.ingest import quality
        from financial_brain.providers.bhavcopy import normalise
        path = cfg.quarantine / "legacy.csv"
        path.write_bytes(normalise(NSE_LEGACY_CSV.encode()))
        with db.connect() as con:
            con.execute("CREATE OR REPLACE TEMP TABLE raw AS SELECT * FROM "
                        f"read_csv_auto('{path.as_posix()}', header=true, all_varchar=true)")
            report = quality.check_bhavcopy(con, "raw", business_date=date(2020, 1, 1),
                                            min_rows=1)
        assert report.publishable and not report.failed


from financial_brain.providers.bhavcopy import UDIFF_COLUMNS as _UC
HDR_UDIFF = ",".join(_UC)


class TestInstrumentIdentity:
    """Regression: 194 days were quarantined as 'duplicates' that were not duplicates.

    BSE lists some securities under two scrip codes sharing one ISIN - IDFC traded as
    both 532659 and 632659 on 2016-12-08, at different closes (57.55 and 59.20). Those
    are two instruments, not a duplicated one. Omitting the exchange's own instrument id
    from the uniqueness key threw away every such day.
    """

    def test_twin_scrip_codes_are_not_duplicates(self, cfg, db):
        from financial_brain.ingest import quality
        from financial_brain.providers.bhavcopy import UDIFF_COLUMNS

        def row(code, close):
            vals = {"TradDt": "2016-12-08", "BizDt": "2016-12-08", "Sgmt": "CM",
                    "Src": "BSE", "FinInstrmTp": "STK", "FinInstrmId": code,
                    "ISIN": "INE043D01016", "TckrSymb": "IDFC", "SctySrs": "A",
                    "FinInstrmNm": "IDFC", "OpnPric": close, "HghPric": close,
                    "LwPric": close, "ClsPric": close, "LastPric": close,
                    "PrvsClsgPric": close, "TtlTradgVol": 100, "TtlTrfVal": 100.0,
                    "TtlNbOfTxsExctd": 5, "SsnId": "F1", "NewBrdLotQty": 1}
            return ",".join(str(vals.get(c, "")) for c in UDIFF_COLUMNS)

        csv_text = ",".join(UDIFF_COLUMNS) + "\n" + row("532659", 57.55) + "\n" + \
            row("632659", 59.20) + "\n"
        path = cfg.quarantine / "twins.csv"
        path.write_text(csv_text, encoding="utf-8")

        with db.connect() as con:
            con.execute("CREATE OR REPLACE TEMP TABLE raw AS SELECT * FROM "
                        f"read_csv_auto('{path.as_posix()}', header=true, all_varchar=true)")
            report = quality.check_bhavcopy(con, "raw", business_date=date(2016, 12, 8),
                                            min_rows=1)
        assert report.publishable, "two scrip codes are two instruments, not a duplicate"
        assert all(r.passed for r in report.results if r.name == "no_duplicate_instrument_rows")

    def test_a_genuine_duplicate_is_still_caught(self, cfg, db):
        from financial_brain.ingest import quality
        from financial_brain.providers.bhavcopy import UDIFF_COLUMNS
        vals = {"TradDt": "2016-12-08", "Sgmt": "CM", "Src": "BSE", "FinInstrmTp": "STK",
                "FinInstrmId": "532659", "ISIN": "INE043D01016", "TckrSymb": "IDFC",
                "SctySrs": "A", "ClsPric": 57.55, "HghPric": 57.55, "LwPric": 57.55}
        line = ",".join(str(vals.get(c, "")) for c in UDIFF_COLUMNS)
        path = cfg.quarantine / "dupe.csv"
        path.write_text(",".join(UDIFF_COLUMNS) + "\n" + line + "\n" + line + "\n",
                        encoding="utf-8")
        with db.connect() as con:
            con.execute("CREATE OR REPLACE TEMP TABLE raw AS SELECT * FROM "
                        f"read_csv_auto('{path.as_posix()}', header=true, all_varchar=true)")
            report = quality.check_bhavcopy(con, "raw", business_date=date(2016, 12, 8),
                                            min_rows=1)
        assert not report.publishable, "the same instrument id twice is a real duplicate"


class TestBsePreIsin:
    """Before 8 Dec 2016 BSE published no ISIN and no date column.

    Scrip codes outlive ISIN changes (884 BSE codes carry more than one ISIN), so each
    mapping must be corroborated by NSE trading the same ISIN, same day, within 3%.
    """

    PRE_ISIN_CSV = (
        "SC_CODE,SC_NAME,SC_GROUP,SC_TYPE,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,NO_TRADES,"
        "NO_OF_SHRS,NET_TURNOV,TDCLOINDI\n"
        "500002,ABB LTD.    ,A ,Q,1250.00,1260.00,1240.00,1255.00,1255.00,1248.00,500,"
        "4000,5000000.00,\n")

    def _zip(self, inner: str, text: str) -> bytes:
        import io
        import zipfile as _zf
        buf = io.BytesIO()
        with _zf.ZipFile(buf, "w") as z:
            z.writestr(inner, text)
        return buf.getvalue()

    def test_date_comes_from_the_inner_filename_and_isin_is_left_blank(self):
        from financial_brain.providers.bhavcopy import normalise
        rows = _rows(normalise(self._zip("EQ020215.CSV", self.PRE_ISIN_CSV)))
        assert rows[0]["TradDt"] == "2015-02-02"
        assert rows[0]["FinInstrmId"] == "500002"
        assert rows[0]["ISIN"] == "", "ISIN must never be guessed inside the parser"

    def _setup(self, con, *, listings, nse):
        for iid, isin, first in listings:
            con.execute("INSERT INTO security_listings VALUES (?, 'BSE', 'X', 'A', ?, ?, ?)",
                        [isin, iid, first, first])
        for isin, close in nse:
            con.execute("""INSERT INTO universe_snapshots
                (business_date, isin, exchange, ticker, series, close_price, tradable)
                VALUES ('2015-02-02', ?, 'NSE', 'X', 'EQ', ?, TRUE)""", [isin, close])
        con.execute("""CREATE OR REPLACE TEMP TABLE raw AS
            SELECT FinInstrmId::VARCHAR AS FinInstrmId, ISIN::VARCHAR AS ISIN,
                   ClsPric::VARCHAR AS ClsPric
            FROM (VALUES ('500002', NULL, '1255.00'), ('999999', NULL, '10.00'))
                 t(FinInstrmId, ISIN, ClsPric)""")

    def test_corroborated_mapping_resolves_and_bse_only_scrip_is_dropped(self, db):
        from financial_brain.ingest import bse_isin
        with db.connect() as con:
            self._setup(con, listings=[("500002", "INE117A01022", date(2016, 12, 8))],
                        nse=[("INE117A01022", 1252.00)])
            assert bse_isin.needs_resolution(con, "raw")
            resolved, unresolved = bse_isin.resolve(con, "raw", date(2015, 2, 2))
            left = con.execute("SELECT FinInstrmId, ISIN FROM raw").fetchall()
        assert (resolved, unresolved) == (1, 1)
        assert left == [("500002", "INE117A01022")], "999999 has no mapping: dropped, not guessed"

    def test_post_split_isin_is_refused_not_mislabelled(self, db):
        """The code's earliest-seen ISIN did not trade on NSE that day -> reject the row."""
        from financial_brain.ingest import bse_isin
        with db.connect() as con:
            self._setup(con, listings=[("500002", "INE117A01030", date(2016, 12, 8))],
                        nse=[("INE117A01022", 1252.00)])   # NSE traded the *old* ISIN
            resolved, _ = bse_isin.resolve(con, "raw", date(2015, 2, 2))
        assert resolved == 0

    def test_price_disagreement_blocks_the_mapping(self, db):
        from financial_brain.ingest import bse_isin
        with db.connect() as con:
            self._setup(con, listings=[("500002", "INE117A01022", date(2016, 12, 8))],
                        nse=[("INE117A01022", 627.50)])    # a 2:1 split apart
            resolved, _ = bse_isin.resolve(con, "raw", date(2015, 2, 2))
        assert resolved == 0

    def test_earliest_seen_isin_is_the_candidate(self, db):
        from financial_brain.ingest import bse_isin
        with db.connect() as con:
            self._setup(con, listings=[("500002", "INE117A01022", date(2016, 12, 8)),
                                       ("500002", "INE117A01030", date(2021, 6, 17))],
                        nse=[("INE117A01022", 1252.00)])
            resolved, _ = bse_isin.resolve(con, "raw", date(2015, 2, 2))
            isin = con.execute("SELECT ISIN FROM raw").fetchone()[0]
        assert resolved == 1 and isin == "INE117A01022"


class TestGapAutoTriage:
    """`fb gaps --auto-review` encodes the rule applied by hand to the first seven gaps.

    1. cross-listed, only one exchange gapped -> price_move (an action moves both)
    2. single-listed -> needs_source (corroboration impossible)
    3. both gapped, no clean ratio -> needs_source
    """

    def _px(self, con, isin, exch, d, close, turnover=5e7):
        con.execute("""INSERT INTO universe_snapshots
            (business_date, isin, exchange, ticker, series, instrument_type, turnover,
             close_price, tradable) VALUES (?, ?, ?, ?, 'EQ', 'STK', ?, ?, TRUE)""",
                    [d, isin, exch, isin[-4:], turnover, close])

    def _world(self, con):
        d1, d2 = date(2020, 3, 2), date(2020, 3, 3)
        # rule 1: listed on both, NSE gaps -50%, BSE flat
        for e in ("NSE", "BSE"):
            self._px(con, "INE000000A01", e, d1, 100.0)
        self._px(con, "INE000000A01", "NSE", d2, 50.0)
        self._px(con, "INE000000A01", "BSE", d2, 100.0)
        # rule 2: NSE only, gaps -60%
        self._px(con, "INE000000B01", "NSE", d1, 100.0)
        self._px(con, "INE000000B01", "NSE", d2, 40.0)
        # rule 3: both gap -43% (no split/bonus ratio gives 0.57)
        for e in ("NSE", "BSE"):
            self._px(con, "INE000000C01", e, d1, 100.0)
            self._px(con, "INE000000C01", e, d2, 57.0)
        # a normal day - must not be touched
        for dd, c in ((d1, 100.0), (d2, 101.0)):
            self._px(con, "INE000000D01", "NSE", dd, c)

    def test_each_rule_gives_its_verdict(self, db):
        from financial_brain.corpactions.detect import auto_triage_gaps
        with db.connect() as con:
            self._world(con)
            out = auto_triage_gaps(con)
            got = dict(con.execute("SELECT isin, verdict FROM gap_reviews").fetchall())
        assert got == {"INE000000A01": "price_move",
                       "INE000000B01": "needs_source",
                       "INE000000C01": "needs_source"}
        assert out == {"events": 3, "action_recorded": 0, "price_move": 1, "needs_source": 2}

    def test_a_gap_on_two_exchanges_is_one_event(self, db):
        from financial_brain.corpactions.detect import auto_triage_gaps
        with db.connect() as con:
            self._world(con)
            auto_triage_gaps(con)
            n, exch = con.execute("SELECT COUNT(*), ANY_VALUE(exchange) FROM gap_reviews "
                                  "WHERE isin = 'INE000000C01'").fetchone()
        assert n == 1 and exch == "BSE+NSE"

    def test_idempotent_and_skips_recorded_actions(self, db):
        from financial_brain.corpactions.detect import auto_triage_gaps
        with db.connect() as con:
            self._world(con)
            con.execute("""INSERT INTO corporate_actions (action_id, isin, action_type,
                ex_date, source, source_tier, observed_at)
                VALUES ('x', 'INE000000C01', 'SPLIT', DATE '2020-03-03', 'test', 1, NOW())""")
            first = auto_triage_gaps(con)
            second = auto_triage_gaps(con)
        assert first["events"] == 2, "a gap already explained by an action is not a gap"
        assert second["events"] == 0, "re-running triages nothing new"

    def test_illiquid_moves_are_ignored(self, db):
        from financial_brain.corpactions.detect import auto_triage_gaps
        with db.connect() as con:
            self._px(con, "INE000000E01", "BSE", date(2020, 3, 2), 100.0, turnover=1000)
            self._px(con, "INE000000E01", "BSE", date(2020, 3, 3), 10.0, turnover=1000)
            assert auto_triage_gaps(con)["events"] == 0


class TestConsecutiveSessions:
    """A comparison is only valid against the exchange's immediately previous session.

    Over 2015-2026 two artefacts produced ~6,000 phantom 'adjustments':
      * weekend special sessions missing from the data (now ingested), and
      * surveillance series transfers: on a stock's first BE day, the LAG within BE is
        whatever BE printed months earlier, while the stated previous close is
        yesterday's EQ close.
    """

    def _days(self):
        return [date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4), date(2026, 3, 5)]

    def test_series_transfer_is_not_a_corporate_action(self, cfg, db):
        from financial_brain.corpactions.detect import find_candidates
        d0, d1, d2, d3 = self._days()
        rows = [(d, "NSE", "INE000000019", "BENCH", "EQ", 100.0, 100.0) for d in (d0, d1, d2, d3)]
        rows += [
            (d0, "NSE", "INE000000027", "SICK", "BE", 50.0, 50.0),    # an old BE print
            (d1, "NSE", "INE000000027", "SICK", "EQ", 100.0, 100.0),
            (d2, "NSE", "INE000000027", "SICK", "EQ", 100.0, 100.0),
            (d3, "NSE", "INE000000027", "SICK", "BE", 101.0, 100.0),  # moved to BE
        ]
        with db.connect() as con:
            _make_eod_view(con, rows)
            found = find_candidates(con)
        assert [c for c in found if c["isin"] == "INE000000027"] == [], \
            "BE's previous row is from d0, not the previous session - nothing to compare"

    def test_a_real_restatement_on_consecutive_sessions_is_still_found(self, cfg, db):
        from financial_brain.corpactions.detect import find_candidates
        d0, d1, d2, d3 = self._days()
        rows = [(d, "NSE", "INE000000019", "BENCH", "EQ", 100.0, 100.0) for d in (d0, d1, d2, d3)]
        rows += [(d2, "NSE", "INE000000035", "SPLT", "EQ", 500.0, 498.0),
                 (d3, "NSE", "INE000000035", "SPLT", "EQ", 101.0, 100.0)]
        with db.connect() as con:
            _make_eod_view(con, rows)
            found = [c for c in find_candidates(con) if c["isin"] == "INE000000035"]
        assert len(found) == 1 and abs(found[0]["factor"] - 0.2) < 1e-9

    def test_rebuild_clears_only_derived_actions(self, cfg, db):
        from financial_brain.corpactions.detect import clear_derived
        with db.connect() as con:
            for aid, derived in (("derived", 0.5), ("from_feed", None)):
                con.execute("""INSERT INTO corporate_actions (action_id, isin, action_type,
                    ex_date, source, source_tier, observed_at, derived_factor)
                    VALUES (?, 'INE000000019', 'SPLIT', DATE '2026-03-03', 'x', 1, NOW(), ?)""",
                            [aid, derived])
            assert clear_derived(con) == 1
            left = [r[0] for r in con.execute("SELECT action_id FROM corporate_actions").fetchall()]
        assert left == ["from_feed"], "a sourced action must never be deleted by a rebuild"


class TestKiteIndexFill:
    """Kite (Tier 2) may fill index days NSE lacks - validated, and never overwriting."""

    def _world(self, con, days, gap):
        for d in days:
            con.execute("""INSERT INTO universe_snapshots (business_date, isin, exchange,
                ticker, series, close_price, tradable)
                VALUES (?, 'INE000000019', 'NSE', 'X', 'EQ', 1, TRUE)""", [d])
            if d != gap:
                con.execute("""INSERT INTO index_levels (business_date, index_name,
                    close_level, variant, source, observed_at)
                    VALUES (?, 'Nifty 50', ?, 'PRICE', 'NSE', NOW())""", [d, 1000.0 + d.day])

    class Fake:
        source, dataset, tier = "KITE", "index_daily", 2

        def __init__(self, bias=0.0):
            self.bias = bias

        def index_tokens(self):
            return {"NIFTY 50": 256265}

        def fetch_range(self, token, start, end):
            import json as _j
            from datetime import timedelta as _td
            candles, d = [], start
            while d <= end:
                c = (1000.0 + d.day) * (1 + self.bias)
                candles.append([f"{d}T00:00:00+0530", c, c, c, c, 0])
                d += _td(days=1)
            body = _j.dumps({"status": "success", "data": {"candles": candles}}).encode()
            return FetchResult(payload=body, url="https://api.kite.test/x",
                               retrieved_at=datetime.now(timezone.utc),
                               filename="k.json", content_type="application/json")

        parse = staticmethod(__import__("financial_brain.providers.kite",
                                        fromlist=["x"]).KiteIndexHistoryProvider.parse)

    def _run(self, cfg, db, bias):
        from financial_brain.ingest import kite_fill
        from financial_brain.lake.store import RawLake
        days = [date(2015, 1, 1) + timedelta(days=i) for i in range(45)]
        gap = days[20]
        with db.connect() as con:
            self._world(con, days, gap)
            out = kite_fill.fill(con, self.Fake(bias), RawLake(cfg.lake))
            got = con.execute("SELECT close_level, source FROM index_levels "
                              "WHERE business_date = ?", [gap]).fetchall()
            nse_untouched = con.execute("SELECT COUNT(*) FROM index_levels "
                                        "WHERE source = 'NSE'").fetchone()[0]
        return out, got, gap, nse_untouched

    def test_fills_only_the_missing_date_when_kite_agrees_with_nse(self, cfg, db):
        out, got, gap, nse = self._run(cfg, db, bias=0.0)
        assert got == [(1000.0 + gap.day, "KITE")]
        assert nse == 44, "NSE Tier-1 levels are never overwritten"
        assert "filled 1 of 1" in out["indices"]["NIFTY 50"]

    def test_refuses_when_kite_disagrees_with_nse(self, cfg, db):
        out, got, _, _ = self._run(cfg, db, bias=0.02)   # a different series, 2% off
        assert got == [], "a mismatched instrument must write nothing"
        assert out["indices"]["NIFTY 50"].startswith("refused")

    def test_missing_credentials_fail_before_any_network_call(self, monkeypatch):
        from financial_brain.providers.kite import KiteNotConfigured, credentials
        monkeypatch.delenv("KITE_API_KEY", raising=False)
        monkeypatch.delenv("KITE_ACCESS_TOKEN", raising=False)
        with pytest.raises(KiteNotConfigured):
            credentials()


class TestGapGuards:
    """Large genuine moves must not be read as corporate actions (11-year findings)."""

    D1, D2 = date(2023, 1, 31), date(2023, 2, 1)

    def _found(self, con, rows):
        from financial_brain.corpactions.detect import find_gap_candidates
        _make_eod_view_with_turnover(con, rows)
        return {c["isin"] for c in find_gap_candidates(con)}

    def test_a_bonus_opens_at_the_adjusted_price_and_is_found(self, cfg, db):
        """Wipro's 1:3 bonus, 2019-03-06: opened 0.755x, closed 0.763x the prior close."""
        with db.connect() as con:
            got = self._found(con, [
                (self.D1, "NSE", "INE075A01022", "WIPRO", "EQ", 400.0, 5e8),
                (self.D2, "NSE", "INE075A01022", "WIPRO", "EQ", 305.2, 5e8, 302.0)])
        assert got == {"INE075A01022"}

    def test_an_intraday_selloff_is_not_an_action(self, cfg, db):
        """Adani Enterprises 2023-02-01: opened 1.007x, closed 0.718x - moved in session."""
        with db.connect() as con:
            got = self._found(con, [
                (self.D1, "NSE", "INE423A01024", "ADANIENT", "EQ", 2975.0, 5e9),
                (self.D2, "NSE", "INE423A01024", "ADANIENT", "EQ", 2135.0, 5e9, 2996.0)])
        assert got == set()

    def test_a_price_rise_is_never_an_inferred_split(self, cfg, db):
        """RCOM 2017-12-20 closed 1.357x - bonuses and splits only lower the price."""
        with db.connect() as con:
            got = self._found(con, [
                (self.D1, "NSE", "INE330H01018", "RCOM", "EQ", 15.0, 5e8),
                (self.D2, "NSE", "INE330H01018", "RCOM", "EQ", 20.0, 5e8, 20.0)])
        assert got == set()

    def test_no_inference_on_a_market_shock_day(self, cfg, db):
        """2020-03-23: Nifty -13%, circuit breaker at the open, banks gapped ~0.75x."""
        with db.connect() as con:
            for d, lvl in ((self.D1, 8745.45), (self.D2, 7610.25)):
                con.execute("""INSERT INTO index_levels (business_date, index_name,
                    close_level, variant, source, observed_at)
                    VALUES (?, 'Nifty 50', ?, 'PRICE', 'NSE', NOW())""", [d, lvl])
            got = self._found(con, [
                (self.D1, "NSE", "INE238A01034", "AXISBANK", "EQ", 400.0, 5e9),
                (self.D2, "NSE", "INE238A01034", "AXISBANK", "EQ", 300.0, 5e9, 300.0)])
        assert got == set()


class TestGapTriageFindings:
    """Shock days and intraday moves are findings (price_move), not open questions."""

    D1, D2 = date(2023, 1, 31), date(2023, 2, 1)

    def _one(self, con, *, open_px, nifty=None):
        t = TestGapAutoTriage()
        t._px(con, "INE423A01024", "NSE", self.D1, 2975.0)
        t._px(con, "INE423A01024", "NSE", self.D2, 1785.0)
        _make_eod_view_with_turnover(con, [
            (self.D1, "NSE", "INE423A01024", "ADANIENT", "EQ", 2975.0, 5e9),
            (self.D2, "NSE", "INE423A01024", "ADANIENT", "EQ", 1785.0, 5e9, open_px)])
        if nifty:
            for d, lvl in zip((self.D1, self.D2), nifty):
                con.execute("""INSERT INTO index_levels (business_date, index_name,
                    close_level, variant, source, observed_at)
                    VALUES (?, 'Nifty 50', ?, 'PRICE', 'NSE', NOW())""", [d, lvl])
        from financial_brain.corpactions.detect import auto_triage_gaps
        auto_triage_gaps(con)
        return con.execute("SELECT verdict, note FROM gap_reviews").fetchone()

    def test_an_intraday_selloff_is_a_price_move(self, cfg, db):
        with db.connect() as con:
            verdict, note = self._one(con, open_px=2996.0)      # opened flat, fell 40% in session
        assert verdict == "price_move" and "during the session" in note

    def test_a_shock_day_is_a_price_move(self, cfg, db):
        with db.connect() as con:
            verdict, note = self._one(con, open_px=1785.0, nifty=(8745.45, 7610.25))
        assert verdict == "price_move" and "market-wide shock" in note

    def test_an_open_gap_on_a_quiet_single_listed_day_stays_open(self, cfg, db):
        with db.connect() as con:
            verdict, _ = self._one(con, open_px=1785.0)
        assert verdict == "needs_source", "action-like but unconfirmable: stays visible"


class TestIsinSuccession:
    """A face-value split changes the ISIN; the scrip code / ticker carries through."""

    D1, D2 = date(2022, 2, 9), date(2022, 2, 10)       # GREENLAM 1:5, 2022-02-10

    def _world(self, con, *, nse_switches=True):
        rows = [  # (exchange, handle-field, isin, first, last, close on that day)
            ("BSE", "538979", "INE544R01013", self.D1, self.D1, 500.0),
            ("BSE", "538979", "INE544R01021", self.D2, self.D2, 100.0),
        ]
        if nse_switches:
            rows += [("NSE", "GREENLAM", "INE544R01013", self.D1, self.D1, 500.0),
                     ("NSE", "GREENLAM", "INE544R01021", self.D2, self.D2, 100.0)]
        for exch, handle, isin, f, l, close in rows:
            iid, tk = (handle, "GREENLAM LTD") if exch == "BSE" else ("", handle)
            con.execute("INSERT INTO security_listings VALUES (?,?,?,?,?,?,?)",
                        [isin, exch, tk, "EQ", iid, f, l])
            con.execute("""INSERT INTO universe_snapshots (business_date, isin, exchange,
                ticker, series, close_price, tradable) VALUES (?,?,?,?, 'EQ', ?, TRUE)""",
                        [f, isin, exch, tk, close])

    def test_detects_a_corroborated_succession_with_its_ratio(self, db):
        from financial_brain.securities import succession
        with db.connect() as con:
            self._world(con)
            found = succession.detect(con)
        assert len(found) == 1
        s = found[0]
        assert (s["old_isin"], s["new_isin"]) == ("INE544R01013", "INE544R01021")
        assert s["effective_date"] == self.D2 and s["confidence"] == "corroborated"
        assert abs(s["price_ratio"] - 0.2) < 1e-9

    def test_one_exchange_is_single_exchange_evidence(self, db):
        from financial_brain.securities import succession
        with db.connect() as con:
            self._world(con, nse_switches=False)
            assert succession.detect(con)[0]["confidence"] == "single_exchange"

    def test_a_non_consecutive_handle_reuse_is_not_a_succession(self, db):
        """A ticker reused months later for an unrelated company must not link them."""
        from financial_brain.securities import succession
        with db.connect() as con:
            for d in (date(2022, 1, 3), date(2022, 1, 4), date(2022, 6, 1)):   # the calendar
                con.execute("""INSERT INTO universe_snapshots (business_date, isin, exchange,
                    ticker, series, close_price, tradable)
                    VALUES (?, 'INE000000019', 'NSE', 'BENCH', 'EQ', 1, TRUE)""", [d])
            for isin, f, l in (("INE000000A01", date(2022, 1, 3), date(2022, 1, 3)),
                               ("INE000000B01", date(2022, 6, 1), date(2022, 6, 1))):
                con.execute("INSERT INTO security_listings VALUES (?, 'NSE', 'REUSED', 'EQ', '', ?, ?)",
                            [isin, f, l])
            assert succession.detect(con) == []

    def test_split_is_recorded_and_explains_the_gap(self, cfg, db):
        from financial_brain.corpactions.detect import auto_triage_gaps, derive_from_successions
        with db.connect() as con:
            self._world(con)
            out = derive_from_successions(con)
            action = con.execute("SELECT isin, ratio_from, ratio_to, confidence "
                                 "FROM corporate_actions").fetchall()
            # the old ISIN still gapped on one exchange that day (NSE kept it a session)
            con.execute("""INSERT INTO universe_snapshots (business_date, isin, exchange,
                ticker, series, turnover, close_price, tradable, instrument_type)
                VALUES (?, 'INE544R01013', 'NSE', 'GREENLAM', 'BE', 5e7, 500, TRUE, 'STK'),
                       (?, 'INE544R01013', 'NSE', 'GREENLAM', 'BE', 5e7, 100, TRUE, 'STK')""",
                        [self.D1, self.D2])
            auto_triage_gaps(con)
            verdict = con.execute("SELECT verdict FROM gap_reviews").fetchone()[0]
        assert out["written"] == 1
        assert action == [("INE544R01021", 1.0, 5.0, "corroborated_succession")]
        assert verdict == "action_recorded"

    def test_identity_only_change_adjusts_nothing(self, db):
        from financial_brain.corpactions.detect import derive_from_successions
        with db.connect() as con:
            self._world(con)
            con.execute("UPDATE universe_snapshots SET close_price = 501 "
                        "WHERE isin = 'INE544R01021'")
            out = derive_from_successions(con)
        assert out["identity_only"] == 1 and out["written"] == 0

    def test_redo_replaces_auto_verdicts_but_never_manual_ones(self, db):
        from financial_brain.corpactions.detect import auto_triage_gaps
        with db.connect() as con:
            con.execute("""INSERT INTO gap_reviews VALUES
                ('INE000000A01', DATE '2020-01-01', 'NSE', 0.5, 'needs_source', 'x', NOW(), 'auto'),
                ('INE000000B01', DATE '2020-01-01', 'NSE', 0.5, 'price_move', 'y', NOW(), 'manual')""")
            auto_triage_gaps(con, redo=True)
            left = con.execute("SELECT isin FROM gap_reviews").fetchall()
        assert left == [("INE000000B01",)]


class TestStaggeredSuccession:
    """Schaeffler 1:5, 2022-02-08: BSE switched ISIN that day; NSE kept the old ISIN one
    more session at the already-split price. The ratio must be measured across the
    ex-date, not old-last -> new-first per exchange (which gave a bogus 3:5)."""

    def test_ratio_is_measured_across_the_first_switch(self, db):
        from financial_brain.securities import succession
        d1, d2, d3 = date(2022, 2, 7), date(2022, 2, 8), date(2022, 2, 9)
        old, new = "INE513A01014", "INE513A01022"
        rows = [  # exchange, handle, isin, date, close
            ("BSE", "505790", old, d1, 8930.85), ("BSE", "505790", new, d2, 1762.45),
            ("BSE", "505790", new, d3, 1708.95),
            ("NSE", "SCHAEFFLER", old, d1, 8933.30), ("NSE", "SCHAEFFLER", old, d2, 1764.90),
            ("NSE", "SCHAEFFLER", new, d3, 1702.50),
        ]
        spans = {}
        with db.connect() as con:
            for exch, h, isin, d, c in rows:
                tk, iid = ("SCHAEFFLER", h) if exch == "BSE" else (h, "")
                con.execute("""INSERT INTO universe_snapshots (business_date, isin, exchange,
                    ticker, series, close_price, tradable) VALUES (?,?,?,?,'EQ',?,TRUE)""",
                            [d, isin, exch, tk, c])
                k = (exch, h, isin, tk, iid)
                spans[k] = (min(spans.get(k, (d, d))[0], d), max(spans.get(k, (d, d))[1], d))
            for (exch, h, isin, tk, iid), (f, l) in spans.items():
                con.execute("INSERT INTO security_listings VALUES (?,?,?, 'EQ', ?,?,?)",
                            [isin, exch, tk, iid, f, l])
            s = succession.detect(con)
        assert len(s) == 1 and s[0]["confidence"] == "corroborated"
        assert s[0]["effective_date"] == d2, "the ex-date is the first switch on either exchange"
        assert abs(s[0]["price_ratio"] - 0.1975) < 0.001, s[0]["price_ratio"]


class TestOneGapDefinition:
    def test_after_triage_nothing_is_untriaged(self, db):
        """Triage and the gate must share one definition of a gap. A drifted copy once
        made the gate report 2,201 'untriaged' gaps that triage had correctly ignored."""
        from financial_brain.corpactions.detect import auto_triage_gaps, find_untriaged_gaps
        with db.connect() as con:
            TestGapAutoTriage()._world(con)
            # plus a stale, non-consecutive comparison that must never count as a gap
            for d, c in ((date(2020, 1, 2), 100.0), (date(2020, 3, 3), 30.0)):
                TestGapAutoTriage()._px(con, "INE000000Z01", "NSE", d, c)
            assert find_untriaged_gaps(con)
            auto_triage_gaps(con)
            assert find_untriaged_gaps(con) == []
