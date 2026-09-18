"""Phase 0 tests.

These run entirely offline against synthetic UDiFF payloads - the network is not a test
dependency. What they assert is the set of properties Phase 0 exists to guarantee:
immutability, idempotence, replayability, quality gating, ISIN identity, point-in-time
correctness, and a cost model that is actually punitive.
"""
from __future__ import annotations

import io
import zipfile
from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.config import Config
from financial_brain.corpactions import actions as ca
from financial_brain.costs.india import CostModel, Segment, Side
from financial_brain.ingest import quality
from financial_brain.ingest.job import (STATUS_OK, STATUS_PARTIAL, STATUS_QUARANTINED,
                                        STATUS_SKIPPED, BhavcopyIngestJob, business_days)
from financial_brain.lake.store import RawLake
from financial_brain.pit.observations import Observation, PITStore
from financial_brain.providers.base import FetchResult, NotPublished
from financial_brain.providers.bhavcopy import UDIFF_COLUMNS
from financial_brain.securities import master as sec
from financial_brain.storage.db import Database
from financial_brain.universe import snapshot as uni

HDR = ",".join(UDIFF_COLUMNS)


def udiff_row(*, trad_dt: str, isin: str, ticker: str, series="EQ", tp="STK",
              instr_id="1", name="TEST LTD", o=100.0, h=110.0, lo=95.0, c=105.0,
              vol=10_000, val=1_050_000.0, trades=250):
    vals = {
        "TradDt": trad_dt, "BizDt": trad_dt, "Sgmt": "CM", "Src": "NSE",
        "FinInstrmTp": tp, "FinInstrmId": instr_id, "ISIN": isin, "TckrSymb": ticker,
        "SctySrs": series, "FinInstrmNm": name, "OpnPric": o, "HghPric": h,
        "LwPric": lo, "ClsPric": c, "LastPric": c, "PrvsClsgPric": o,
        "SttlmPric": c, "TtlTradgVol": vol, "TtlTrfVal": val,
        "TtlNbOfTxsExctd": trades, "SsnId": "F1", "NewBrdLotQty": 1,
    }
    return ",".join(str(vals.get(col, "")) for col in UDIFF_COLUMNS)


def udiff_csv(trad_dt: str, n: int = 600, **overrides) -> bytes:
    rows = [HDR]
    for i in range(n):
        rows.append(udiff_row(trad_dt=trad_dt, isin=f"INE{i:06d}01{i % 10}",
                              ticker=f"SYM{i:04d}", instr_id=str(500000 + i), **overrides))
    return ("\n".join(rows) + "\n").encode()


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(data_root=tmp_path).ensure()


@pytest.fixture
def db(cfg) -> Database:
    d = Database(cfg)
    d.migrate()
    return d


class FakeProvider:
    """Stands in for a real exchange. Serves prepared payloads by date."""
    source, dataset, tier = "NSE", "bhavcopy_cm", 1

    def __init__(self, payloads: dict[date, bytes]):
        self.payloads = payloads
        self.fetches = 0

    def fetch(self, business_date: date) -> FetchResult:
        if business_date not in self.payloads:
            raise NotPublished(f"holiday {business_date}")
        self.fetches += 1
        return FetchResult(
            payload=self.payloads[business_date],
            url=f"https://example.test/{business_date}.csv",
            retrieved_at=datetime.now(timezone.utc),
            filename=f"bhav_{business_date:%Y%m%d}.csv",
            content_type="text/csv",
        )

    @staticmethod
    def to_csv(payload: bytes) -> bytes:
        return payload


def make_job(cfg, payloads) -> BhavcopyIngestJob:
    job = BhavcopyIngestJob("NSE", cfg)
    job.provider = FakeProvider(payloads)
    return job


# ----------------------------------------------------------------- raw lake
class TestRawLake:
    def test_stores_payload_with_provenance(self, cfg):
        lake = RawLake(cfg.lake)
        obj = lake.put(source="NSE", dataset="bhavcopy_cm", business_date=date(2026, 9, 11),
                       filename="a.csv", payload=b"hello", url="https://x.test/a.csv")
        assert obj.sha256 == (
            "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824")
        assert obj.size_bytes == 5
        assert lake.read(obj) == b"hello"
        assert lake.meta(obj.key).url == "https://x.test/a.csv"

    def test_identical_rewrite_is_noop(self, cfg):
        lake = RawLake(cfg.lake)
        kw = dict(source="NSE", dataset="d", business_date=date(2026, 9, 11),
                  filename="a.csv", url="u")
        a = lake.put(payload=b"same", **kw)
        b = lake.put(payload=b"same", **kw)
        assert a.key == b.key
        assert len(list(lake.iter_objects())) == 1

    def test_changed_bytes_never_overwrite_history(self, cfg):
        """A source republishing different bytes is a finding, not a silent update."""
        lake = RawLake(cfg.lake)
        kw = dict(source="NSE", dataset="d", business_date=date(2026, 9, 11),
                  filename="a.csv", url="u")
        a = lake.put(payload=b"original", **kw)
        b = lake.put(payload=b"restated", **kw)
        assert a.key != b.key
        assert lake.read(a) == b"original"      # the original is still there
        assert lake.read(b) == b"restated"
        assert len(list(lake.iter_objects())) == 2

    def test_detects_corruption(self, cfg):
        lake = RawLake(cfg.lake)
        obj = lake.put(source="NSE", dataset="d", business_date=date(2026, 9, 11),
                       filename="a.csv", payload=b"good", url="u")
        (cfg.lake / obj.key).write_bytes(b"tampered")
        with pytest.raises(IOError, match="hash mismatch"):
            lake.read(obj)


# ----------------------------------------------------------------- ingestion
class TestIngestion:
    def test_happy_path(self, cfg, db):
        d = date(2026, 9, 11)
        job = make_job(cfg, {d: udiff_csv("2026-09-11")})
        res = job.run(d)
        assert res.status == STATUS_OK, res.message
        assert res.rows_out == 600

        with db.connect() as con:
            assert con.execute("SELECT COUNT(*) FROM securities").fetchone()[0] == 600
            assert con.execute("SELECT COUNT(*) FROM universe_snapshots").fetchone()[0] == 600
            assert con.execute("SELECT COUNT(*) FROM lake_manifest").fetchone()[0] == 1

    def test_is_idempotent(self, cfg, db):
        d = date(2026, 9, 11)
        job = make_job(cfg, {d: udiff_csv("2026-09-11")})
        assert job.run(d).status == STATUS_OK
        second = job.run(d)
        assert second.status == STATUS_SKIPPED
        assert job.provider.fetches == 1        # no second network call

    def test_replay_from_lake_needs_no_network(self, cfg, db):
        d = date(2026, 9, 11)
        job = make_job(cfg, {d: udiff_csv("2026-09-11")})
        job.run(d)
        fetches_after_first = job.provider.fetches

        job.provider.payloads = {}              # network now returns nothing at all

        # A day already published is skipped even from the lake; replay needs --force.
        assert job.run(d, from_lake=True).status == "skipped"

        res = job.run(d, force=True, from_lake=True)
        assert res.status == STATUS_OK
        assert res.rows_out == 600
        assert job.provider.fetches == fetches_after_first

    def test_holiday_is_not_a_failure(self, cfg, db):
        job = make_job(cfg, {})
        res = job.run(date(2026, 9, 11))
        assert res.status == "not_published"
        assert res.ok

    def test_bad_payload_is_quarantined_not_published(self, cfg, db):
        """The whole point of the gate: bad data never reaches curated."""
        d = date(2026, 9, 11)
        bad = HDR.encode() + b"\n" + udiff_row(
            trad_dt="2026-09-11", isin="INE000000019", ticker="X",
            h=50.0, lo=100.0).encode() + b"\n"       # high < low
        job = make_job(cfg, {d: bad})
        res = job.run(d)

        assert res.status == STATUS_QUARANTINED
        with db.connect() as con:
            assert con.execute("SELECT COUNT(*) FROM universe_snapshots").fetchone()[0] == 0
            assert con.execute("SELECT COUNT(*) FROM securities").fetchone()[0] == 0
        assert list((cfg.quarantine / "NSE").glob("*.csv")), "payload kept for diagnosis"

    def test_row_scope_failure_rejects_the_row_and_keeps_the_day(self, cfg, db):
        """Regression for BSE 2026-07-29.

        BSE published Maple Infrastructure Trust with open=high=low=144.00 and
        close=142.50 - a close outside the day's own range. Discarding 4,929 good rows
        over one impossible one would be the wrong trade, so row-scope failures reject
        the row and publish the rest.
        """
        d = date(2026, 9, 11)
        good = udiff_csv("2026-09-11", n=600).decode()
        impossible = udiff_row(trad_dt="2026-09-11", isin="INE777777019",
                               ticker="BADROW", o=144.0, h=144.0, lo=144.0, c=142.5)
        job = make_job(cfg, {d: (good + impossible + chr(10)).encode()})
        res = job.run(d)

        assert res.status == STATUS_PARTIAL
        assert res.rows_rejected == 1
        assert res.rows_out == 600, "every good row still published"

        with db.connect() as con:
            assert con.execute(
                "SELECT COUNT(*) FROM universe_snapshots WHERE isin = 'INE777777019'"
            ).fetchone()[0] == 0, "the impossible row must not reach curated"
            rej = con.execute("SELECT ticker, reject_reason FROM rejected_rows").fetchall()
        assert rej == [("BADROW", "ohlc_coherent")], "and it is kept, with a reason"

    def test_wrong_date_is_rejected(self, cfg, db):
        d = date(2026, 9, 11)
        job = make_job(cfg, {d: udiff_csv("2026-09-10")})   # yesterday's file
        assert job.run(d).status == STATUS_QUARANTINED

    def test_every_run_is_recorded(self, cfg, db):
        d = date(2026, 9, 11)
        make_job(cfg, {d: udiff_csv("2026-09-11")}).run(d)
        with db.connect() as con:
            runs = con.execute("SELECT status, lake_key FROM ingest_runs").fetchall()
        assert len(runs) == 1 and runs[0][0] == STATUS_OK and runs[0][1]

    def test_weekends_are_candidates_because_markets_do_open(self):
        """Budget Saturday 2025-02-01 was a full session; the exchange decides, not us."""
        days = list(business_days(date(2025, 1, 31), date(2025, 2, 3)))  # Fri..Mon
        assert days == [date(2025, 1, 31), date(2025, 2, 1), date(2025, 2, 2),
                        date(2025, 2, 3)]

    def test_a_republished_file_is_not_a_session(self, cfg, db):
        """BSE re-served Friday 2020-06-19 as Saturday 2020-06-20: every close and
        non-zero volume identical. That is not a session and must not be published."""
        fri, sat = date(2020, 6, 19), date(2020, 6, 20)
        job = make_job(cfg, {fri: udiff_csv("2020-06-19"),
                             sat: udiff_csv("2020-06-20")})   # same prices, same volumes
        assert job.run(fri).status == STATUS_OK
        res = job.run(sat)
        assert res.status == "not_published"
        assert "republication" in res.message

    def test_a_quiet_real_session_is_not_mistaken_for_a_republication(self, cfg, db):
        """Unchanged closes alone are normal; unchanged volumes everywhere are not."""
        d1, d2 = date(2026, 9, 10), date(2026, 9, 11)
        job = make_job(cfg, {d1: udiff_csv("2026-09-10"),
                             d2: udiff_csv("2026-09-11", vol=12_345)})
        job.run(d1)
        assert job.run(d2).status == STATUS_OK

    def test_a_weekend_with_no_session_is_not_a_failure(self, cfg, db):
        job = make_job(cfg, {})
        assert job.run(date(2026, 9, 12)).status == "not_published"   # a plain Saturday


# ---------------------------------------------------------- security master
class TestSecurityMaster:
    def test_isin_is_the_key_across_a_symbol_change(self, cfg, db):
        """The property that makes ISIN primary: a rename must not create a new security."""
        isin = "INE999999019"
        d1, d2 = date(2026, 9, 10), date(2026, 9, 11)
        p1 = HDR.encode() + b"\n" + udiff_row(trad_dt="2026-09-10", isin=isin,
                                              ticker="OLDNAME").encode() + b"\n"
        p2 = HDR.encode() + b"\n" + udiff_row(trad_dt="2026-09-11", isin=isin,
                                              ticker="NEWNAME").encode() + b"\n"
        job = make_job(cfg, {d1: p1, d2: p2})
        # min_rows would reject a 1-row file, so gate on the checks we care about here
        job.run(d1, force=True)
        with db.connect() as con:
            con.execute("DELETE FROM dq_results")

        with db.connect() as con:
            for payload, d in ((p1, d1), (p2, d2)):
                con.execute(
                    "CREATE OR REPLACE TEMP TABLE raw AS SELECT * FROM "
                    f"read_csv_auto('{_write(cfg, payload)}', header=true, all_varchar=true)")
                sec.upsert_from_bhavcopy(con, "raw", d, "NSE")

            assert con.execute("SELECT COUNT(*) FROM securities").fetchone()[0] == 1
            changes = sec.symbol_changes(con)
            assert len(changes) == 1
            assert changes[0]["n_tickers"] == 2

            found = sec.resolve(con, "OLDNAME", on_date=d1)
            assert found and found[0]["isin"] == isin
            assert sec.resolve(con, "OLDNAME", on_date=d2) == []   # not that ticker then

    def test_cross_listing_joins_on_isin(self, cfg, db):
        isin, d = "INE111111019", date(2026, 9, 11)
        with db.connect() as con:
            for exch in ("NSE", "BSE"):
                payload = HDR.encode() + b"\n" + udiff_row(
                    trad_dt="2026-09-11", isin=isin, ticker=f"{exch}SYM").encode() + b"\n"
                con.execute("CREATE OR REPLACE TEMP TABLE raw AS SELECT * FROM "
                            f"read_csv_auto('{_write(cfg, payload)}', header=true, all_varchar=true)")
                sec.upsert_from_bhavcopy(con, "raw", d, exch)
            both = sec.cross_listed(con)
        assert len(both) == 1 and both[0]["exchanges"] == "BSE+NSE"


def _write(cfg, payload: bytes) -> str:
    p = cfg.quarantine / f"_t{abs(hash(payload)) % 10**8}.csv"
    p.write_bytes(payload)
    return p.as_posix()


# ----------------------------------------------------------------- universe
class TestUniverse:
    def test_churn_exposes_survivorship(self, cfg, db):
        """A name that disappears is exactly what a current-universe backtest cannot see."""
        d1, d2 = date(2026, 9, 10), date(2026, 9, 11)
        keep = udiff_row(trad_dt="2026-09-10", isin="INE000000019", ticker="KEEP")
        gone = udiff_row(trad_dt="2026-09-10", isin="INE000000027", ticker="GONE")
        day2 = udiff_row(trad_dt="2026-09-11", isin="INE000000019", ticker="KEEP")

        with db.connect() as con:
            for payload, d in (((HDR + "\n" + keep + "\n" + gone).encode(), d1),
                               ((HDR + "\n" + day2).encode(), d2)):
                con.execute("CREATE OR REPLACE TEMP TABLE raw AS SELECT * FROM "
                            f"read_csv_auto('{_write(cfg, payload)}', header=true, all_varchar=true)")
                uni.write_snapshot(con, "raw", d, "NSE")

            out = uni.churn(con, d1, d2, exchange="NSE")
        assert out["at_start"] == 2 and out["at_end"] == 1
        assert out["disappeared"] == ["INE000000027"]

    def test_universe_as_of_is_point_in_time(self, cfg, db):
        d = date(2026, 9, 11)
        make_job(cfg, {d: udiff_csv("2026-09-11")}).run(d)
        with db.connect() as con:
            assert len(uni.as_of(con, d)) == 600
            assert uni.as_of(con, date(2026, 9, 10)) == []   # nothing known on a day we lack


# -------------------------------------------------------------- PIT store
class TestPITStore:
    def test_as_of_hides_the_future(self, cfg, db):
        """The property the whole store exists for."""
        with db.connect() as con:
            store = PITStore(con)
            published = datetime(2026, 8, 15, tzinfo=timezone.utc)
            store.record(Observation(
                entity_key="INE000000019", attribute="revenue", value_num=100.0,
                period_end=date(2026, 6, 30), published_at=published, source="FILING"))

            before = store.as_of(datetime(2026, 8, 14, tzinfo=timezone.utc))
            after = store.as_of(datetime(2026, 8, 16, tzinfo=timezone.utc))

        assert before == [], "a fact must be invisible before it was published"
        assert len(after) == 1 and after[0]["value_num"] == 100.0

    def test_restatement_never_destroys_the_original(self, cfg, db):
        with db.connect() as con:
            store = PITStore(con)
            first = store.record(Observation(
                entity_key="INE000000019", attribute="revenue", value_num=100.0,
                period_end=date(2026, 6, 30),
                published_at=datetime(2026, 8, 15, tzinfo=timezone.utc), source="FILING"))
            store.revise(first, Observation(
                entity_key="INE000000019", attribute="revenue", value_num=92.0,
                period_end=date(2026, 6, 30),
                published_at=datetime(2026, 11, 1, tzinfo=timezone.utc), source="FILING"))

            mid = store.as_of(datetime(2026, 9, 1, tzinfo=timezone.utc))
            now = store.as_of(datetime(2026, 12, 1, tzinfo=timezone.utc))
            trail = store.history("INE000000019", "revenue")

        assert mid[0]["value_num"] == 100.0, "a backtest in September must see the old number"
        assert now[0]["value_num"] == 92.0, "today must see the restated number"
        assert len(trail) == 2, "both versions are retained"

    def test_identical_facts_are_not_duplicated(self, cfg, db):
        with db.connect() as con:
            store = PITStore(con)
            o = Observation(entity_key="X", attribute="eps", value_num=5.0,
                            period_end=date(2026, 6, 30), source="FILING",
                            published_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
            assert store.record(o) == store.record(o)
            assert store.coverage()["observations"] == 1

    def test_source_tier_is_recorded(self, cfg, db):
        with db.connect() as con:
            store = PITStore(con)
            store.record(Observation(entity_key="X", attribute="eps", value_num=1.0,
                                     source="FILING"))
            store.record(Observation(entity_key="Y", attribute="eps", value_num=1.0,
                                     source="SCREENER"))
            tiers = dict(con.execute(
                "SELECT entity_key, source_tier FROM pit_observations").fetchall())
        assert tiers["X"] == 1 and tiers["Y"] == 3


# ------------------------------------------------------- corporate actions
class TestCorporateActions:
    def test_split_factor(self):
        f = ca.price_factor({"action_type": "SPLIT", "ratio_from": 1, "ratio_to": 5})
        assert f == pytest.approx(0.2)

    def test_bonus_factor(self):
        # 1:1 bonus - one new share for each held - halves the price
        f = ca.price_factor({"action_type": "BONUS", "ratio_from": 1, "ratio_to": 1})
        assert f == pytest.approx(0.5)

    def test_non_price_actions_have_no_factor(self):
        assert ca.price_factor({"action_type": "SYMBOL_CHANGE"}) is None

    def test_adjustment_applies_only_before_ex_date(self, cfg, db):
        isin = "INE000000019"
        with db.connect() as con:
            for i, d in enumerate([date(2026, 9, 9), date(2026, 9, 10), date(2026, 9, 11)]):
                con.execute(
                    """INSERT INTO universe_snapshots
                       (business_date, isin, exchange, ticker, series, instrument_type,
                        close_price, tradable) VALUES (?,?,?,?,?,?,?,?)""",
                    [d, isin, "NSE", "T", "EQ", "STK", 500.0 if i < 2 else 100.0, True])
            ca.record(con, isin=isin, action_type="SPLIT", ex_date=date(2026, 9, 11),
                      ratio_from=1, ratio_to=5, source="NSE")
            ca.rebuild_adjustment_factors(con)
            rows = {r["business_date"]: r for r in ca.adjusted_prices(con, isin)}

        assert rows[date(2026, 9, 10)]["close_adjusted"] == pytest.approx(100.0)
        assert rows[date(2026, 9, 11)]["close_adjusted"] == pytest.approx(100.0)
        assert rows[date(2026, 9, 10)]["close_unadjusted"] == pytest.approx(500.0), \
            "the traded price remains a fact"

    def test_suspicious_gap_detection_finds_unrecorded_actions(self, cfg, db):
        isin = "INE000000019"
        with db.connect() as con:
            for d, px in [(date(2026, 9, 10), 500.0), (date(2026, 9, 11), 100.0)]:
                con.execute(
                    """INSERT INTO universe_snapshots
                       (business_date, isin, exchange, ticker, series, instrument_type,
                        close_price, turnover, tradable) VALUES (?,?,?,?,?,?,?,?,?)""",
                    [d, isin, "NSE", "T", "EQ", "STK", px, 5_000_000.0, True])
            found = ca.detect_suspicious_gaps(con, threshold=0.2)
        assert len(found) == 1 and found[0]["isin"] == isin


# --------------------------------------------------------------- cost model
class TestCostModel:
    def test_delivery_charges_stt_on_both_legs(self):
        m = CostModel()
        buy = m.one_way(turnover=100_000, side=Side.BUY, segment=Segment.DELIVERY,
                        include_impact=False)
        sell = m.one_way(turnover=100_000, side=Side.SELL, segment=Segment.DELIVERY,
                         include_impact=False)
        assert buy["stt"] > 0 and sell["stt"] > 0
        assert buy["stt"] == pytest.approx(sell["stt"])

    def test_intraday_charges_stt_on_sell_only(self):
        m = CostModel()
        assert m.one_way(turnover=100_000, side=Side.BUY, segment=Segment.INTRADAY)["stt"] == 0
        assert m.one_way(turnover=100_000, side=Side.SELL, segment=Segment.INTRADAY)["stt"] > 0

    def test_stamp_duty_is_buy_side_only(self):
        m = CostModel()
        assert m.one_way(turnover=100_000, side=Side.BUY)["stamp"] > 0
        assert m.one_way(turnover=100_000, side=Side.SELL)["stamp"] == 0

    def test_illiquid_names_cost_far_more(self):
        m = CostModel()
        mega = m.round_trip(turnover=100_000, bucket="mega")["bps"]
        micro = m.round_trip(turnover=100_000, bucket="micro")["bps"]
        assert micro > mega * 5, "impact must dominate outside the liquid core"

    def test_high_turnover_strategies_are_punished(self):
        """The number that kills most Indian factor strategies before they start."""
        m = CostModel()
        monthly = m.annual_drag(turnover_per_year=12, bucket="mid")
        assert monthly > 0.05, "12x annual turnover in mid-caps should cost >5% a year"

    def test_components_are_itemised(self):
        c = CostModel().one_way(turnover=100_000, side=Side.BUY)
        for part in ("stt", "exchange", "sebi", "stamp", "brokerage", "gst", "impact"):
            assert part in c
        assert c["total"] == pytest.approx(sum(c[p] for p in
                                               ("stt", "exchange", "sebi", "stamp",
                                                "brokerage", "gst", "impact")))


# ------------------------------------------------------------------ quality
class TestQualityContracts:
    def test_all_checks_pass_on_clean_data(self, cfg, db):
        with db.connect() as con:
            con.execute("CREATE OR REPLACE TEMP TABLE raw AS SELECT * FROM "
                        f"read_csv_auto('{_write(cfg, udiff_csv('2026-09-11'))}', "
                        "header=true, all_varchar=true)")
            report = quality.check_bhavcopy(con, "raw", business_date=date(2026, 9, 11))
        assert report.publishable and not report.failed and not report.warnings

    def test_malformed_isin_is_an_error(self, cfg, db):
        payload = (HDR + "\n" + udiff_row(trad_dt="2026-09-11", isin="NOTANISIN",
                                          ticker="X")).encode()
        with db.connect() as con:
            con.execute("CREATE OR REPLACE TEMP TABLE raw AS SELECT * FROM "
                        f"read_csv_auto('{_write(cfg, payload)}', header=true, all_varchar=true)")
            report = quality.check_bhavcopy(con, "raw", business_date=date(2026, 9, 11),
                                            min_rows=1)
        # A malformed ISIN is a *row* problem: the row is rejected, the file still publishes.
        assert report.publishable, "one bad row must not condemn the whole file"
        assert any(r.name == "isin_well_formed" for r in report.row_level)
