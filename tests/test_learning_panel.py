"""The terminal panel for the learning loop and the data collection never breaks the page and never leaks markup."""
from __future__ import annotations

import duckdb

from financial_brain.paper import learning_panel as LP
from financial_brain.paper import loss_miner as LM
from financial_brain.paper import loss_review as LR
from financial_brain.paper import shadow as SH


def _ledger():
    con = duckdb.connect(":memory:")
    con.execute(LR.SCHEMA)
    con.execute(LM.SCHEMA)
    con.execute(SH.TABLES)
    return con


def test_empty_ledger_degrades_to_plain_sentences(tmp_path):
    out = LP.html(duckdb.connect(":memory:"), archive=tmp_path / "none.duckdb")
    assert "No loss review has run yet" in out and "not started" in out


def test_miner_waiting_for_sessions_is_explained(tmp_path):
    con = _ledger()
    con.execute("INSERT INTO loss_mining_runs VALUES ('2026-11-12', 14, 0, 0, 0, 0, 0, 'INSUFFICIENT', '')")
    out = LP.html(con, archive=tmp_path / "none.duckdb")
    assert "needs 40 sessions" in out and "has 14" in out and "no skip rule is registered" in out


def test_confirmed_patterns_are_surfaced_and_escaped(tmp_path):
    con = _ledger()
    con.execute("INSERT INTO hypotheses VALUES ('<script>x</script>', 'a', 'b', 50, 20, -30.5, -2.4, 0.4, 12, 'CONFIRMED', 'n')")
    out = LP.html(con, archive=tmp_path / "none.duckdb")
    assert "needs your decision" in out and "&lt;script&gt;" in out and "<script>" not in out


def test_shadow_rows_are_shown(tmp_path):
    con = _ledger()
    con.execute("INSERT INTO shadow_results VALUES ('2027-05-01', 'SK-0001', 70, 120, -9000, -3000, -3.1, 4.0, 5000, 14000, 0, 'PASSING')")
    out = LP.html(con, archive=tmp_path / "none.duckdb")
    assert "SK-0001" in out and "PASSING" in out and "14,000" in out


def test_archive_progress_is_reported(tmp_path):
    from financial_brain.ingest import quote_archive as QA
    con = QA.connect(tmp_path / "d.duckdb")
    con.execute("""INSERT INTO quotes SELECT TIMESTAMP '2026-11-12 09:15:00' + INTERVAL (i) MINUTE, 'S' || (j % 300),
                   100, 100, 10, 5, 1, 1, 1, 1, 99.9, 100.1, 1, 1, 5, 5 FROM range(380) t(i), range(300) u(j)""")
    con.close()
    out = LP.html(_ledger(), archive=tmp_path / "d.duckdb")
    assert "1 complete session" in out and "of 120" in out
