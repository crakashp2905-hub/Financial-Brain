"""Phase 1 - index lineage and the Market Regime Brain."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from financial_brain.config import Config
from financial_brain.storage.db import Database


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(data_root=tmp_path).ensure()


@pytest.fixture
def db(cfg) -> Database:
    d = Database(cfg)
    d.migrate()
    return d


def _lvl(con, name, d, close, open_=None):
    con.execute("""INSERT INTO index_levels (business_date, index_name, open_level,
        close_level, variant, source, observed_at)
        VALUES (?, ?, ?, ?, 'PRICE', 'NSE', NOW())""", [d, name, open_ or close, close])


FRI, MON = date(2015, 11, 6), date(2015, 11, 9)       # the CNX -> Nifty rebrand


class TestIndexLineage:
    def _rebrand(self, con):
        # Bihar-result Monday: the whole family gapped ~-2% at the open
        for old, new, lvl in (("CNX Nifty", "Nifty 50", 7954.3), ("CNX Bank", "Nifty Bank", 16932.0),
                              ("CNX IT", "Nifty IT", 11600.0)):
            _lvl(con, old, FRI, lvl)
            _lvl(con, new, MON, lvl * 0.98, open_=lvl * 0.98)

    def test_a_family_wide_gap_does_not_block_genuine_renames(self, db):
        from financial_brain.indices import lineage
        with db.connect() as con:
            self._rebrand(con)
            out = lineage.record(con)
            con.execute(lineage.CANONICAL_VIEW)
            n50 = con.execute("SELECT MIN(business_date), COUNT(*) FROM index_levels_canonical "
                              "WHERE index_name = 'Nifty 50'").fetchone()
        assert out["verified"] == 3
        assert n50 == (FRI, 2), "one continuous Nifty 50 series across the rename"

    def test_a_pair_that_does_not_move_with_its_family_is_rejected(self, db):
        from financial_brain.indices import lineage
        with db.connect() as con:
            self._rebrand(con)
            _lvl(con, "CNX Alpha Index", FRI, 10000.0)
            _lvl(con, "Nifty Alpha 50", MON, 10121.0)          # +1.2% while family -2%
            out = lineage.record(con)
        assert [r["old_name"] for r in out["rejected"]] == ["CNX Alpha Index"]

    def test_a_stray_early_day_under_the_later_name_does_not_break_the_chain(self, db):
        """NSE published 'NIFTY Midcap 100' once on 2016-07-07, two years early."""
        from financial_brain.indices import lineage
        with db.connect() as con:
            _lvl(con, "Nifty Free Float Midcap 100", date(2016, 7, 6), 14000.0)
            _lvl(con, "NIFTY Midcap 100", date(2016, 7, 7), 14095.35)       # the stray day
            _lvl(con, "Nifty Free Float Midcap 100", date(2018, 3, 28), 18757.0)
            _lvl(con, "NIFTY Midcap 100", date(2018, 4, 2), 19097.4)
            out = lineage.record(con)
            con.execute(lineage.CANONICAL_VIEW)
            rows = con.execute("SELECT COUNT(*), COUNT(DISTINCT business_date) FROM "
                               "index_levels_canonical WHERE index_name = 'NIFTY Midcap 100'"
                               ).fetchone()
        assert out["verified"] == 1
        assert rows == (4, 4), "every date once, under the current name"
