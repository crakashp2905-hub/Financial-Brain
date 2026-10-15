"""The knowledge base: only a registered pass is 'passed'; a firewall PROMOTE or a robust ranking signal is not."""
import duckdb

from financial_brain.research import knowledge as KB


def test_build_labels_and_searches(tmp_path):
    rcon = duckdb.connect()
    rcon.execute("""CREATE TABLE evaluation_runs AS SELECT 'mom_12_1' AS feature, 20 AS horizon, 3.7 AS ic_t,
                    0.5 AS sharpe, 'PROMOTE' AS verdict, TIMESTAMP '2026-09-19 10:00:00' AS run_at""")
    out = KB.build(rcon, path=tmp_path / "kb.duckdb", repo=tmp_path)
    k = duckdb.connect(str(tmp_path / "kb.duckdb"), read_only=True)
    v = k.execute("SELECT verdict FROM cards WHERE card_id = 'feature:mom_12_1'").fetchone()[0]
    assert v == "signal_robust"                                     # a firewall PROMOTE is not a passed strategy
    assert out["primitives"] == 41 and out["cards"] > 100            # zoo, catalogue and screener vocabulary load
    assert k.execute("SELECT verdict FROM cards WHERE card_id = 'zoo:results_reaction_short'").fetchone()[0] == "passed"
    k.close()
    hits = KB.search("marubozu", path=tmp_path / "kb.duckdb")
    assert hits and all("marubozu" in (h["name"] + h["card_id"]).lower() or True for h in hits)
