"""Phase 1 - evidence ledger: immutable, idempotent, traceable to bytes."""
from __future__ import annotations

from datetime import date, datetime

import pytest

from financial_brain.config import Config
from financial_brain.evidence import ledger
from financial_brain.lake.store import RawLake
from financial_brain.storage.db import Database


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(data_root=tmp_path).ensure()


@pytest.fixture
def db(cfg) -> Database:
    d = Database(cfg)
    d.migrate()
    return d


def _claim(**kw):
    base = dict(kind="index_close", subject="Nifty 50", as_of=datetime(2026, 9, 18, 15, 30),
                claim="Nifty 50 closed at 25,114.00", value={"close": 25114.0},
                source="NSE", source_tier=1, derivation="ind_close_all v1")
    base.update(kw)
    return base


def test_reminting_the_same_claim_is_a_no_op(db):
    with db.connect() as con:
        a = ledger.mint(con, **_claim())
        b = ledger.mint(con, **_claim())
        n = con.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
    assert a == b and n == 1


def test_a_different_value_is_a_different_claim(db):
    with db.connect() as con:
        a = ledger.mint(con, **_claim())
        b = ledger.mint(con, **_claim(value={"close": 25115.0}))
    assert a != b


def test_a_correction_supersedes_without_erasing(db):
    with db.connect() as con:
        old = ledger.mint(con, **_claim())
        new = ledger.correct(con, old, **_claim(value={"close": 25120.0},
                                                claim="Nifty 50 closed at 25,120.00"))
        n = con.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
        t = ledger.trace(con, old)
        assert ledger.current(con, old) == new
    assert n == 2, "the original claim is kept - past decisions replay against it"
    assert t["superseded_by"] == new


def test_trace_reaches_the_stored_bytes_and_every_user(cfg, db):
    lake = RawLake(cfg.lake)
    obj = lake.put(source="NSE", dataset="index_close", business_date=date(2026, 9, 18),
                   filename="ind_close_all_18092026.csv", payload=b"Index Name,Closing Index Value\n",
                   url="https://nsearchives.nseindia.com/content/indices/ind_close_all_18092026.csv")
    with db.connect() as con:
        con.execute("""INSERT INTO lake_manifest (key, source, dataset, business_date, filename,
            url, retrieved_at, sha256, size_bytes, http_status, content_type)
            VALUES (?,?,?,?,?,?,?,?,?,200,'text/csv')""",
                    [obj.key, obj.source, obj.dataset, obj.business_date, obj.filename,
                     obj.url, obj.retrieved_at, obj.sha256, obj.size_bytes])
        eid = ledger.mint(con, **_claim(lake_key=obj.key))
        ledger.use(con, [eid, eid], used_by_kind="brief", used_by_id="2026-09-19")
        t = ledger.trace(con, eid)
    assert t["sha256"] == obj.sha256 and t["url"].endswith("18092026.csv")
    assert [u["used_by_id"] for u in t["used_by"]] == ["2026-09-19"], "recorded once"
