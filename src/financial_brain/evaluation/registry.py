"""Pre-registered hypotheses: say what you will test, and when, before you look.

The firewall deflates by every trial ever run, so re-testing variations until one passes
cannot work - but it also means each trial is expensive. This registry is how a trial is
spent well:

1. ``register`` freezes the hypothesis - signal, direction, horizon, book size, expected
   edge, rationale - with the **data cutoff** that existed at that moment. Its id is a
   hash of the spec: the same idea cannot be registered twice under different names.
2. ``test(mode="in_sample")`` runs the Implementability Gate, then the firewall on data
   up to the cutoff. **Once.** A second in-sample test of the same hypothesis is refused.
3. ``test(mode="out_of_sample")`` runs the firewall only on rebalances *after* the
   cutoff - data nobody had seen when the hypothesis was written. It is refused until at
   least ``MIN_OOS_DATES`` rebalances exist. This is the result that counts.

Every outcome is appended to ``hypothesis_tests``; nothing is overwritten.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone

from . import firewall, implementability

MIN_OOS_DATES = 12
REQUIRED = {"name", "signal", "direction", "horizon", "positions", "aum_inr", "rationale"}


class RegistryError(ValueError):
    pass


def register(con, spec: dict) -> str:
    missing = REQUIRED - set(spec)
    if missing:
        raise RegistryError(f"spec needs {sorted(missing)}")
    if spec["signal"] not in firewall.benchmark.FEATURES:
        raise RegistryError(f"unknown signal {spec['signal']!r}")
    if spec["direction"] not in (1, -1):
        raise RegistryError("direction must be 1 or -1")
    body = {k: spec[k] for k in sorted(spec) if k != "name"}
    hid = "hy_" + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
    if con.execute("SELECT 1 FROM hypotheses WHERE hypothesis_id = ?", [hid]).fetchone():
        raise RegistryError(f"already registered as {hid}")
    cutoff = con.execute("SELECT MAX(business_date) FROM features").fetchone()[0]
    con.execute("""INSERT INTO hypotheses (hypothesis_id, name, spec, data_cutoff,
                   registered_at) VALUES (?,?,?,?,?)""",
                [hid, spec["name"], json.dumps(spec, sort_keys=True), cutoff,
                 datetime.now(timezone.utc)])
    return hid


def _spec(con, hid: str) -> tuple[dict, date]:
    r = con.execute("SELECT spec, data_cutoff FROM hypotheses WHERE hypothesis_id = ?",
                    [hid]).fetchone()
    if not r:
        raise RegistryError(f"unknown hypothesis {hid}")
    return json.loads(r[0]), r[1]


def test(con, hid: str, mode: str = "in_sample") -> dict:
    spec, cutoff = _spec(con, hid)
    if mode not in ("in_sample", "out_of_sample"):
        raise RegistryError("mode is in_sample or out_of_sample")
    if mode == "in_sample" and con.execute(
            """SELECT 1 FROM hypothesis_tests WHERE hypothesis_id = ?
               AND mode = 'in_sample'""", [hid]).fetchone():
        raise RegistryError("the in-sample test has been spent; only out-of-sample remains")

    h = implementability.Hypothesis(
        name=spec["name"], signal=spec["signal"], rebalance_days=spec["horizon"],
        positions=spec["positions"], aum_inr=spec["aum_inr"],
        needs_short=spec.get("needs_short", False),
        expected_edge_per_rebalance=spec.get("expected_edge_per_rebalance"))
    gate = implementability.check(con, h, as_of=cutoff)
    result: dict = {"hypothesis_id": hid, "mode": mode, "implementability": gate}
    if gate["verdict"] == implementability.FAIL:
        result["verdict"] = "REJECT"
        result["reasons"] = [f"implementability: {k} - {v['why']}"
                             for k, v in gate["checks"].items() if v["result"] == "FAIL"]
    else:
        window = ({"end": str(cutoff)} if mode == "in_sample"
                  else {"start": str(cutoff + timedelta(days=1))})
        if mode == "out_of_sample":
            # Count first with the benchmark, which records nothing: too little new data
            # is not a trial and must not spend one.
            n = firewall.benchmark.evaluate(con, spec["signal"], spec["horizon"],
                                            direction=spec["direction"], **window)["dates"]
            if n < MIN_OOS_DATES:
                raise RegistryError(f"only {n} out-of-sample rebalances since {cutoff}; "
                                    f"need {MIN_OOS_DATES}")
        fw = firewall.validate(con, spec["signal"], spec["horizon"],
                               direction=spec["direction"], **window)
        result.update(verdict=fw["verdict"], reasons=fw["reasons"], firewall=fw)
    con.execute("""INSERT INTO hypothesis_tests (hypothesis_id, mode, tested_at, verdict,
                   result) VALUES (?,?,?,?,?)""",
                [hid, mode, datetime.now(timezone.utc), result["verdict"],
                 json.dumps(result, default=str)])
    return result
