"""The fine-tuning dataset, which is mostly a test of the split.

Everything here is about leakage, because a leaked split produces a model whose numbers cannot be
interpreted and whose training cannot be undone. Three leaks, in increasing subtlety: a random split,
a per-name split, and a chronological split with no gap at the boundary.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pytest

from financial_brain.forecasting import dataset as D
from financial_brain.forecasting.distribution import ForecastError

START = date(2016, 1, 4)


def _db(*, sessions=1600, names=4):
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE adjusted_prices (business_date DATE, lineage VARCHAR,
                   isin VARCHAR, close_raw DOUBLE, close_adj DOUBLE, factor DOUBLE)""")
    con.execute("""CREATE TABLE eod_prices (business_date DATE, isin VARCHAR, series VARCHAR,
                   exchange VARCHAR, open_price DOUBLE, high_price DOUBLE, low_price DOUBLE,
                   close_price DOUBLE, traded_volume BIGINT, turnover DOUBLE)""")
    con.execute("CREATE TABLE features (business_date DATE, lineage VARCHAR, adv20 DOUBLE)")
    con.execute("""
        CREATE TEMP TABLE raw AS
        SELECT CAST(? AS DATE) + CAST(s.k AS INTEGER) AS business_date,
               'L' || CAST(n.i AS VARCHAR) AS lineage,
               'INE' || LPAD(CAST(n.i AS VARCHAR), 9, '0') AS isin,
               100.0 * POWER(1.0004, s.k) * (1 + 0.01 * (((s.k + n.i) % 11) - 5)) AS raw,
               1.0 AS factor,
               1e9 - n.i AS adv
        FROM generate_series(0, ? - 1) AS s(k), generate_series(0, ? - 1) AS n(i)
    """, [START, sessions, names])
    con.execute("""INSERT INTO adjusted_prices
                   SELECT business_date, lineage, isin, raw, raw, factor FROM raw""")
    con.execute("INSERT INTO features SELECT business_date, lineage, adv FROM raw")
    con.execute("""INSERT INTO eod_prices
                   SELECT business_date, isin, 'EQ', 'NSE', raw * 0.99, raw * 1.02,
                          raw * 0.98, raw, 100000, 1e9 FROM raw""")
    return con


def _cal(con):
    return [r[0] for r in con.execute(
        "SELECT DISTINCT business_date FROM adjusted_prices ORDER BY 1").fetchall()]


def _plan(con, **kw):
    cal = _cal(con)
    return D.splits(start=cal[0], end=cal[-1], sessions=cal, **kw)


# --------------------------------------------------------------------------- the split
def test_the_splits_are_chronological_and_never_overlap():
    con = _db()
    p = _plan(con)
    assert p["train"].end < p["validation"].start
    assert p["validation"].end < p["out_of_sample"].start
    assert p["train"].start == _cal(con)[0]
    assert p["out_of_sample"].end == _cal(con)[-1]


def test_a_purge_gap_separates_every_boundary_so_targets_cannot_become_inputs():
    """A training window ending the session before the boundary predicts sessions that lie inside
    validation. Without a gap the last training targets are the first validation inputs."""
    con = _db()
    p = _plan(con, predict_window=20)
    cal = _cal(con)
    gap = p["gap_sessions"]
    assert gap == 20 * D.EMBARGO_MULTIPLE

    for lo, hi in p["purged"]:
        held = len([d for d in cal if lo <= d <= hi])
        assert held == gap, f"gap {lo}..{hi} holds {held}, not {gap}"

    # And nothing in a purge gap belongs to any split.
    for lo, hi in p["purged"]:
        for sp in (p["train"], p["validation"], p["out_of_sample"]):
            assert not (sp.contains(lo) or sp.contains(hi))


def test_the_boundary_is_placed_on_sessions_not_on_calendar_span():
    """Otherwise the boundary drifts with the holiday calendar, and two runs over the same window
    disagree about which sessions were trained on."""
    con = _db()
    cal = _cal(con)
    p = D.splits(start=cal[0], end=cal[-1], sessions=cal, train_frac=0.65)
    trained = [d for d in cal if p["train"].contains(d)]
    assert len(trained) == int(len(cal) * 0.65)


def test_a_split_without_the_exchange_calendar_is_refused():
    con = _db()
    cal = _cal(con)
    with pytest.raises(ForecastError, match="exchange calendar"):
        D.splits(start=cal[0], end=cal[-1])


def test_fractions_that_leave_no_holdout_are_refused():
    con = _db()
    cal = _cal(con)
    with pytest.raises(ForecastError, match="no out-of-sample"):
        D.splits(start=cal[0], end=cal[-1], sessions=cal, train_frac=0.8, val_frac=0.25)


def test_a_window_too_short_for_two_gaps_is_refused_rather_than_squeezed():
    con = _db(sessions=1600)
    cal = _cal(con)[:60]
    with pytest.raises(ForecastError, match="gaps"):
        D.splits(start=cal[0], end=cal[-1], sessions=cal, predict_window=20)


# ------------------------------------------------------------------------- the audit
def test_the_audit_passes_a_sound_plan_and_counts_rows_in_each_split():
    con = _db()
    p = _plan(con)
    lineages = ["L0", "L1", "L2", "L3"]
    a = D.audit(con, p, lineages)
    assert a["clean"] is True and a["problems"] == []
    assert a["rows"]["train"] > a["rows"]["validation"] > 0
    assert a["rows"]["out_of_sample"] > 0
    # The three splits plus two gaps account for every session, times four names.
    assert (a["rows"]["train"] + a["rows"]["validation"] + a["rows"]["out_of_sample"]
            + 2 * p["gap_sessions"] * len(lineages)) == len(_cal(con)) * len(lineages)


def test_the_audit_catches_a_boundary_that_was_hand_edited_into_overlapping():
    con = _db()
    p = _plan(con)
    cal = _cal(con)
    p["validation"] = D.Split("validation", p["train"].end - timedelta(days=5), cal[-50])
    a = D.audit(con, p, ["L0"])
    assert a["clean"] is False
    assert any("train ends after validation starts" in x for x in a["problems"])


def test_the_audit_catches_a_gap_that_is_too_narrow():
    con = _db()
    p = _plan(con)
    first = p["purged"][0]
    # Shrink the first gap to a single session.
    p["purged"] = [(first[0], first[0]), p["purged"][1]]
    a = D.audit(con, p, ["L0"])
    assert a["clean"] is False
    assert any("purge gap" in x for x in a["problems"])


# ------------------------------------------------------------------------- the universe
def test_the_training_universe_is_chosen_as_of_the_start_of_training():
    """Choosing it as of today selects names that survived and grew, and teaches the model what a
    winner looks like."""
    con = _db()
    cal = _cal(con)
    con.execute("UPDATE features SET adv20 = 1.0 WHERE lineage = 'L0' AND business_date >= ?",
                [cal[800]])
    early = D.universe(con, as_of=cal[10], names=4, min_adv=1e7)
    late = D.universe(con, as_of=cal[-1], names=4, min_adv=1e7)
    assert "L0" in early, "L0 was the most liquid name at the start"
    assert "L0" not in late


# ---------------------------------------------------------------------------- the export
def test_the_out_of_sample_split_is_not_exported_by_default():
    """A holdout that gets exported gets looked at, and a holdout that gets looked at is a
    validation set with a misleading name."""
    con = _db()
    p = _plan(con)
    with pytest.raises(ForecastError, match="refusing to export"):
        D.export(con, "/tmp/never", split=p["out_of_sample"], lineages=["L0"])


def test_an_export_writes_kronos_columns_and_stays_inside_its_split(tmp_path):
    con = _db()
    p = _plan(con)
    m = D.export(con, tmp_path, split=p["train"], lineages=["L0", "L1"], min_bars=100)
    assert m["lineages_written"] == 2
    assert m["keyed_on"] == "lineage" and m["adjusted"] is True

    rows = list(csv_rows(tmp_path / "train" / "L0.csv"))
    assert rows[0] == list(D.COLUMNS)
    dates = [date.fromisoformat(r[0]) for r in rows[1:]]
    assert min(dates) >= p["train"].start
    assert max(dates) <= p["train"].end, "an export must not reach past its own split"
    # high >= close >= low on every row, so the adjustment was applied consistently.
    for r in rows[1:]:
        o, h, lo, c = float(r[1]), float(r[2]), float(r[3]), float(r[4])
        assert lo <= c <= h and lo <= o <= h


def test_a_lineage_with_too_little_history_is_skipped_and_named(tmp_path):
    con = _db()
    p = _plan(con)
    cal = _cal(con)
    con.execute("DELETE FROM eod_prices WHERE isin = 'INE000000001' AND business_date < ?",
                [cal[700]])
    m = D.export(con, tmp_path, split=p["train"], lineages=["L0", "L1"], min_bars=500)
    assert m["lineages_written"] == 1
    assert "L1" in m["skipped"]
    assert not (Path(tmp_path) / "train" / "L1.csv").exists()


def test_the_export_leaves_a_manifest_saying_how_the_bars_were_made(tmp_path):
    con = _db()
    p = _plan(con)
    D.export(con, tmp_path, split=p["train"], lineages=["L0"], min_bars=100)
    m = json.loads((Path(tmp_path) / "train" / "manifest.json").read_text())
    assert m["exchange_series"] == "NSE EQ"
    assert "lineage" in m["note"] and "succession" in m["note"]
    assert m["rows"] > 0


def csv_rows(path):
    import csv as _csv
    with open(path, newline="", encoding="utf-8") as fh:
        yield from _csv.reader(fh)
