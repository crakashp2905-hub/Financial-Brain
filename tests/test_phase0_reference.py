"""Tests for the Phase 0 gap-closers: reference data, benchmarks, derived corporate
actions, and the point-in-time history CLI path.

Offline, like the rest of the suite - parsers are exercised against captured payload
shapes rather than the network.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from financial_brain.config import Config
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
        series VARCHAR, instrument_type VARCHAR, close_price DOUBLE, prev_close DOUBLE)""")
    for r in rows:
        con.execute("INSERT INTO _eod VALUES (?,?,?,?,?,'STK',?,?)", list(r))
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
        series VARCHAR, instrument_type VARCHAR, close_price DOUBLE,
        prev_close DOUBLE, turnover DOUBLE)""")
    for d, exch, isin, tk, ser, close, turnover in rows:
        con.execute("INSERT INTO _eod2 VALUES (?,?,?,?,?,'STK',?,NULL,?)",
                    [d, exch, isin, tk, ser, close, turnover])
    con.execute("CREATE OR REPLACE TEMP VIEW eod_prices AS SELECT * FROM _eod2")


class TestPlausibleRatios:
    def test_absurd_ratios_are_not_plausible(self):
        """A generic closest-fraction search called 0.8383 a '16:19 split'."""
        assert (16, 19) not in detect.PLAUSIBLE_RATIOS
        assert (12, 7) not in detect.PLAUSIBLE_RATIOS

    def test_real_split_and_bonus_ratios_are_plausible(self):
        for ratio in [(1, 2), (1, 5), (1, 10), (2, 3), (3, 2), (1, 6), (1, 3)]:
            assert ratio in detect.PLAUSIBLE_RATIOS
