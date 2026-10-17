"""A data fingerprint ignores row order and changes when any value changes; every recorded trial has a script."""
import duckdb

from financial_brain.evaluation import standard as S
from financial_brain.research import reproduce as RP


def test_fingerprint_order_independent_and_value_sensitive():
    a, b = duckdb.connect(), duckdb.connect()
    a.execute("CREATE TABLE t AS SELECT * FROM (VALUES (DATE '2026-01-01', 1.0), (DATE '2026-01-02', 2.0)) v(business_date, x)")
    b.execute("CREATE TABLE t AS SELECT * FROM (VALUES (DATE '2026-01-02', 2.0), (DATE '2026-01-01', 1.0)) v(business_date, x)")
    fa, fb = RP.fingerprint(a, ["t"]), RP.fingerprint(b, ["t"])
    assert fa == fb and fa["t"]["rows"] == 2 and fa["t"]["last"] == "2026-01-02"
    b.execute("UPDATE t SET x = 2.5 WHERE x = 2.0")
    assert RP.fingerprint(b, ["t"])["t"]["hash"] != fa["t"]["hash"]
    assert RP.fingerprint(a, ["nope"])["nope"] == {"missing": True}


def test_every_2026q4_trial_has_a_script_in_the_repo():
    names = {r["name"] for r in S.ledger() if r.get("kind") == "trial" and r.get("date", "") >= "2026-10-13"
             and r.get("verdict")}
    assert names <= set(RP.TRIALS)
    for script, _ in RP.TRIALS.values():
        assert (RP.SCRIPTS / script).exists()
