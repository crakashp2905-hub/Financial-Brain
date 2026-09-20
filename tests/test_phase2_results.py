"""As-reported financials parsed from results PDFs (C03/C11).

The dataset that cannot be bought back later: what a company reported on the day, kept
point in time. These tests pin the two things that make it trustworthy - labels matched
through OCR damage, and a statement that must pass its own arithmetic before it is
stored - and the append-only rule that makes "what did we know then" answerable.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from financial_brain.config import Config
from financial_brain.docintel import results as parse
from financial_brain.ingest import results as ing
from financial_brain.storage.db import Database


@pytest.mark.parametrize("label,field", [
    ("Revenue from operations", "revenue"),
    ("Other income (Refer note-5)", "other_income"),
    ("Total Income", "total_income"),
    ("Total Expenses", "total_expenses"),
    # Real lines from a scanned filing: a row number, OCR damage, a formula reference.
    ("13 Net Proflt/(Loss) for the Period/Year (9+12)", "pat"),
    ("5 Profltl(loss) before Tax and Exceptional Items (3+4)", "pbt"),
    ("Value of Sales & Setvices (Revenue)", None),
])
def test_labels_survive_ocr_damage_and_decoration(label, field):
    assert parse.match(label) == field


@pytest.mark.parametrize("line,not_a_field", [
    ("Cost of materials consumed", None), ("- Power and Fuel", None),
    ("Total tax", None), ("Finance costs", None)])
def test_other_statement_lines_are_not_mistaken_for_headline_fields(line, not_a_field):
    assert parse.match(line) is not_a_field


def test_units_and_basis_and_period_are_read_from_the_heading():
    head = ("Reliance Industries Limited UNAUDITED CONSOLIDATED FINANCIAL RESULTS FOR "
            "THE QUARTER ENDED 301H JUNE, 2026 (Rs in crore, except per share data)")
    assert parse.unit_of(head) == 10 ** 7
    assert parse.basis_of(head) == "consolidated"
    assert parse.period_of(head) == date(2026, 6, 30)
    assert parse.basis_of("STANDALONE RESULTS") == "standalone"
    assert parse.unit_of("(Rs. in lakhs)") == 10 ** 5


def test_a_statement_must_pass_its_own_arithmetic():
    """A misread column shows up as a sum that does not add up. A wrong revenue figure
    is worse than a missing one, so it is not stored."""
    good = parse.Statement(basis="consolidated", period_end=date(2026, 6, 30),
                           unit=10 ** 7,
                           values={"revenue": 298621.0, "other_income": 4447.0,
                                   "total_income": 303068.0, "total_expenses": 275873.0,
                                   "pbt": 27195.0})
    good.checks = parse._check(good.values, good.unit)
    assert good.ok()

    bad = parse.Statement(basis="consolidated", period_end=date(2026, 6, 30),
                          unit=10 ** 7,
                          values={"revenue": 298621.0, "other_income": 4447.0,
                                  "total_income": 111111.0})
    bad.checks = parse._check(bad.values, bad.unit)
    assert not bad.ok()


def test_a_statement_with_nothing_to_check_is_not_trusted():
    st = parse.Statement(basis="standalone", period_end=date(2026, 6, 30), unit=1,
                         values={"revenue": 100.0})
    st.checks = parse._check(st.values, st.unit)
    assert not st.ok(), "no arithmetic to verify means no verification"


def _con(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    return db


def _row(con, *, filed, revenue, period=date(2026, 6, 30)):
    st = parse.Statement(basis="consolidated", period_end=period, unit=10 ** 7,
                         values={"revenue": revenue, "other_income": 1.0,
                                 "total_income": revenue + 1})
    st.checks = {"income adds up": True}
    ing._store(con, isin="INE000A01001", company="Acme", news_id=f"n{filed.day}", st=st,
               lake_key="k", filed_at=filed, url="u")


def test_a_restatement_is_a_new_row_not_an_update(tmp_path):
    with _con(tmp_path).connect() as con:
        _row(con, filed=datetime(2026, 8, 1, 18, 0), revenue=100.0)
        _row(con, filed=datetime(2026, 11, 5, 18, 0), revenue=90.0)   # restated lower
        rows = con.execute("""SELECT filed_at, revenue FROM financial_results
                              ORDER BY filed_at""").fetchall()
        assert len(rows) == 2, "the original report is never overwritten"
        assert [r[1] / 10 ** 7 for r in rows] == [100.0, 90.0]


def test_as_known_on_returns_what_was_published_by_that_date(tmp_path):
    """The query a backtest must use: on 1 September the restatement had not happened."""
    with _con(tmp_path).connect() as con:
        _row(con, filed=datetime(2026, 8, 1, 18, 0), revenue=100.0)
        _row(con, filed=datetime(2026, 11, 5, 18, 0), revenue=90.0)
        then = ing.as_known_on(con, "INE000A01001", date(2026, 9, 1))
        now = ing.as_known_on(con, "INE000A01001", date(2026, 12, 1))
        assert [r["revenue"] / 10 ** 7 for r in then] == [100.0]
        assert [r["revenue"] / 10 ** 7 for r in now] == [90.0]


def test_a_statement_without_a_period_is_not_stored(tmp_path):
    with _con(tmp_path).connect() as con:
        st = parse.Statement(basis="consolidated", period_end=None, unit=10 ** 7,
                             values={"revenue": 1.0})
        assert ing._store(con, isin="INE000A01001", company="Acme", news_id="n1", st=st,
                          lake_key="k", filed_at=datetime(2026, 8, 1), url="u") is None
        assert con.execute("SELECT COUNT(*) FROM financial_results").fetchone()[0] == 0
