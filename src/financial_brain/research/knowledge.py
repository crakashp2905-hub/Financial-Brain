"""The knowledge base: one evidence card for every signal, indicator, rule, anomaly and strategy this project has
built or tested, with every test it was ever in - failures included, because a failure is what stops the next search
from re-testing a dead idea.

Stored in its own file, ``data/knowledge.duckdb`` (rebuildable from the sources below at any time):

    cards       card_id, name, kind, family, source, side_claim, n_tests, verdict, best_t, summary, refs
    tests       card_id, origin, horizon, stat, value_a, value_b, verdict, tested_on, detail (JSON)
    primitives  the vocabulary the combination agent may combine: name, label, side, kind, card verdict

Sources, all already in the repository or the data directory:

    feature library sweep     data/feature_library_sweep.parquet   (163 features x horizons, FDR, held-out t)
    firewall ledger           evaluation_runs                       (151 factor trials)
    ADR-0004 trial ledger     docs/trials/ledger.jsonl              (per-rule results inside family trials)
    edge result files         data/edges/*_result.json              (depth: IC by tier, long-short, null)
    anomaly catalogue         research/catalogue.py                 (63 published anomalies, papers, priors)
    paper zoo                 paper/zoo_edges.VERDICT, paper/zoo.KIND
    screener vocabulary       paper/signals.EVENTS / STATES / SIGNED

Verdicts are normalised to:

    passed           cleared a registered trading bar (net of costs, both halves, held out) - today only N2
    candidate        positive net in both halves, short of the bar (M1, C1, ...)
    signal_robust    orders names robustly (rank IC significant, replicated, held out) - which says nothing about
                     whether trading it pays, nor which way: several run opposite to the trader's claim
    signal_no_edge   orders names in-sample but does not hold up
    failed / untested
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

PATH = Path("data") / "knowledge.duckdb"
SCHEMA = """
CREATE OR REPLACE TABLE cards (card_id VARCHAR PRIMARY KEY, name VARCHAR, kind VARCHAR, family VARCHAR, source VARCHAR,
    side_claim VARCHAR, n_tests INTEGER, verdict VARCHAR, best_t DOUBLE, summary VARCHAR, refs VARCHAR);
CREATE OR REPLACE TABLE tests (card_id VARCHAR, origin VARCHAR, horizon VARCHAR, stat VARCHAR, value_a DOUBLE,
    value_b DOUBLE, verdict VARCHAR, tested_on VARCHAR, detail VARCHAR);
CREATE OR REPLACE TABLE primitives (name VARCHAR PRIMARY KEY, label VARCHAR, side VARCHAR, kind VARCHAR, verdict VARCHAR);
"""

#: The registered result note behind each ADR-0004 trial, for the card's references.
NOTES = {"M1": "Result of the cost-aware momentum book", "F3": "Result of the short build-up avoid filter",
         "C1": "Result of edges batch three", "N2F": "Result of edges batch three", "EAP": "Result of edges batch three",
         "BAB": "Result of betting against beta in futures", "K7": "Result of chart and candle setups in futures",
         "D1": "Result of delivery share", "B4": "Result of index mean reversion and VWAP",
         "A10": "Result of published price anomalies"}


def _family(name: str) -> str:
    from ..evaluation import families
    f = families.family_of(name)
    if f != families.UNASSIGNED:
        return f
    n = name.lower()
    for key, fam in (("fvg", "smart-money"), ("sweep", "smart-money"), ("bos", "smart-money"), ("order_block", "smart-money"),
                     ("c_", "candles"), ("candle", "candles"), ("gap", "gaps"), ("fib", "levels"), ("pivot", "levels"),
                     ("gann", "levels"), ("harmonic", "patterns"), ("gartley", "patterns"), ("wyckoff", "patterns"),
                     ("vol", "volatility"), ("atr", "volatility"), ("rsi", "reversal"), ("stoch", "reversal"),
                     ("ma", "momentum"), ("ichi", "momentum"), ("supertrend", "momentum"), ("psar", "momentum"),
                     ("volume", "volume"), ("obv", "volume"), ("delivery", "volume"), ("amihud", "liquidity"),
                     ("illiq", "liquidity"), ("carry", "carry"), ("basis", "carry"), ("results", "events"),
                     ("earnings", "events"), ("announcement", "events")):
        if key in n:
            return fam
    return "other"


def _ledger_rows(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()] if path.exists() else []


def build(rcon, *, path: Path = PATH, repo: Path = Path(".")) -> dict:
    cards: dict[str, dict] = {}
    tests: list[dict] = []

    def card(cid, name, kind, *, source="", side="", summary="", refs=""):
        c = cards.setdefault(cid, {"card_id": cid, "name": name, "kind": kind, "family": _family(name), "source": source,
                                   "side_claim": side, "summary": summary, "refs": refs})
        for k, v in (("source", source), ("side_claim", side), ("summary", summary), ("refs", refs)):
            if v and not c.get(k):
                c[k] = v
        return c

    def test(cid, origin, horizon, stat, a, b, verdict, on, detail=None):
        tests.append({"card_id": cid, "origin": origin, "horizon": str(horizon), "stat": stat,
                      "value_a": None if a is None or not np.isfinite(a) else float(a),
                      "value_b": None if b is None or not np.isfinite(b) else float(b),
                      "verdict": verdict, "tested_on": str(on), "detail": json.dumps(detail or {}, default=str)})

    # 1. feature library sweep
    sw = repo / "data" / "feature_library_sweep.parquet"
    if sw.exists():
        for r in pd.read_parquet(sw).itertuples():
            cid = f"feature:{r.feature}"
            card(cid, r.feature, "indicator", source="features/library.py",
                 refs="Result of the deep feature library")
            v = "signal_robust" if r.counts else "signal_no_edge" if r.fdr else "failed"
            test(cid, "deep library sweep (rank IC, FDR, held-out)", r.h, "IC t / held-out t", r.t, r.held_out_t, v,
                 "2026-10", {"tiers": [r.t_large, r.t_next, r.t_mid, r.t_small], "fdr": bool(r.fdr)})
    # 2. firewall trials
    for f, h, ict, sharpe, verdict, at in rcon.execute(
            "SELECT feature, horizon, ic_t, sharpe, verdict, run_at FROM evaluation_runs").fetchall():
        cid = f"feature:{f}"
        card(cid, f, "factor" if not f.startswith(("event_", "candle_")) else "event", source="evaluation/firewall.py")
        # A firewall PROMOTE was a pass under a bar the ledger has since outgrown (every one was later rejected or
        # superseded by ADR-0004): recorded as a robust signal, not a passed strategy.
        test(cid, "firewall", h, "IC t / net Sharpe", ict, sharpe, "signal_robust" if verdict == "PROMOTE" else "failed",
             str(at)[:10])
    # 3. ADR-0004 trials (and the rules inside family trials)
    for row in _ledger_rows(repo / "docs" / "trials" / "ledger.jsonl"):
        if row.get("kind") != "trial":
            continue
        code = row["name"].split()[0]
        note = NOTES.get(code, "")
        inner = row.get("setups") or row.get("rules") or row.get("anomalies")
        if inner:
            for k, v in inner.items():
                cid = f"rule:{k}"
                card(cid, k, "trading rule", source=row.get("prereg", ""), refs=note)
                ok = (v.get("t_a") or 0) >= 2 and (v.get("t_b") or 0) >= 2
                pos = (v.get("mean_a") or 0) > 0 and (v.get("mean_b") or 0) > 0
                test(cid, row["name"], "", "t 2016-21 / 2022-26", v.get("t_a"), v.get("t_b"),
                     "passed" if ok else "candidate" if pos else "failed", row["date"], v)
        else:
            cid = f"strategy:{code}"
            card(cid, row["name"], "strategy", source=row.get("prereg", ""), refs=note)
            pos = (row.get("mean_a") or 0) > 0 and (row.get("mean_b") or 0) > 0
            v = "passed" if row.get("verdict") == "CANDIDATE" else "candidate" if pos else "failed"
            test(cid, row["name"], "", "t 2016-21 / 2022-26", row.get("t_a"), row.get("t_b"), v, row["date"],
                 {k: row.get(k) for k in ("g4_stat", "g4_null_q95", "turnover", "failed")})
    # depth for the anomalies
    a10 = repo / "data" / "edges" / "a10_result.json"
    if a10.exists():
        for k, v in json.loads(a10.read_text()).items():
            if k.startswith("_") or "depth" not in v:
                continue
            d = v["depth"]
            test(f"rule:{k}", "anomaly depth (rank IC, IC by tier, long-short)", "monthly", "IC t / LS gross t",
                 d["ic"][1], d["ls_gross"][1], "signal_no_edge" if abs(d["ic"][1] or 0) >= 3 else "failed", "2026-10-14",
                 d)
    # 4. the published-anomaly catalogue
    from . import catalogue as CAT
    for x in [x for v in vars(CAT).values() if isinstance(v, list) for x in v if isinstance(x, dict) and "status" in x]:
        cid = f"anomaly:{x['name']}"
        card(cid, x["name"], "published anomaly", source=x["source"], side="high is good" if x["direction"] > 0
             else "low is good", summary=x["claim"])
        if x["status"] != "ready":
            test(cid, "catalogue", "", "data", None, None, "untested", "", {"needs": x["needs"], "status": x["status"]})
    # 5. the paper zoo
    from ..paper import zoo as Z
    from ..paper import zoo_edges as ZE
    for name, kind in Z.KIND.items():
        cid = f"zoo:{name}"
        verdict_text = ZE.VERDICT.get(name, "")
        card(cid, name, f"paper strategy ({kind})", source="paper/zoo.py", summary=verdict_text)
        # "out-of-sample only" registrations were chosen after a result: no evidence yet, so untested until G5 says.
        v = ("passed" if verdict_text.startswith("passed") else "untested" if verdict_text.startswith("out-of-sample")
             else "candidate" if "forward test" in verdict_text
             else "failed" if verdict_text.startswith("failed") else "untested")
        test(cid, "zoo verdict", "", "text", None, None, v, "", {"verdict": verdict_text})
    # 6. screener vocabulary = the agent's primitives
    from ..paper import signals as SG
    prims = [{"name": k, "label": lab, "side": side, "kind": "event"} for k, (lab, side) in SG.EVENTS.items()]
    prims += [{"name": k, "label": lab, "side": "both", "kind": "state flip"} for k, lab in SG.STATES.items()]
    prims += [{"name": k, "label": lab, "side": "both", "kind": "signed event"} for k, lab in SG.SIGNED.items()]
    for p in prims:
        card(f"feature:{p['name']}", p["name"], "indicator", source="features/library.py", side=p["side"],
             summary=p["label"])

    # verdict and best t per card
    T = pd.DataFrame(tests)
    rank = {"passed": 5, "candidate": 4, "signal_robust": 3, "signal_no_edge": 2, "failed": 1, "untested": 0}
    for cid, c in cards.items():
        t = T[T["card_id"] == cid] if len(T) else T
        c["n_tests"] = int(len(t[t["verdict"] != "untested"])) if len(t) else 0
        c["verdict"] = max(t["verdict"], key=lambda v: rank.get(v, 0)) if len(t) else "untested"
        vals = pd.concat([t["value_a"], t["value_b"]]).dropna() if len(t) else pd.Series(dtype=float)
        c["best_t"] = float(vals.abs().max()) if len(vals) and np.isfinite(vals.abs().max()) else None
    for p in prims:
        p["verdict"] = cards[f"feature:{p['name']}"]["verdict"]

    path.parent.mkdir(parents=True, exist_ok=True)
    k = duckdb.connect(str(path))
    try:
        k.execute(SCHEMA)
        C = pd.DataFrame(list(cards.values()))[["card_id", "name", "kind", "family", "source", "side_claim", "n_tests",
                                                 "verdict", "best_t", "summary", "refs"]]
        k.register("_c", C)
        k.execute("INSERT INTO cards SELECT * FROM _c")
        if len(T):
            k.register("_t", T)
            k.execute("INSERT INTO tests SELECT * FROM _t")
        k.register("_p", pd.DataFrame(prims))
        k.execute("INSERT INTO primitives SELECT name, label, side, kind, verdict FROM _p")
        out = {"cards": len(C), "tests": len(T), "primitives": len(prims),
               "by_verdict": C["verdict"].value_counts().to_dict(), "by_kind": C["kind"].value_counts().to_dict(),
               "built": str(date.today())}
    finally:
        k.close()
    return out


def search(query: str, *, path: Path = PATH, limit: int = 25) -> list[dict]:
    k = duckdb.connect(str(path), read_only=True)
    try:
        q = f"%{query.lower()}%"
        return k.execute("""SELECT card_id, name, kind, family, verdict, n_tests, round(best_t, 2) AS best_t, refs
                            FROM cards WHERE lower(name) LIKE ? OR lower(family) LIKE ? OR lower(summary) LIKE ?
                               OR lower(kind) LIKE ?
                            ORDER BY CASE verdict WHEN 'passed' THEN 0 WHEN 'candidate' THEN 1 WHEN 'signal_robust'
                                     THEN 2 WHEN 'signal_no_edge' THEN 3 WHEN 'failed' THEN 4 ELSE 5 END,
                                     best_t DESC NULLS LAST LIMIT ?""",
                         [q, q, q, q, limit]).df().to_dict("records")
    finally:
        k.close()


def card_tests(card_id: str, *, path: Path = PATH) -> list[dict]:
    k = duckdb.connect(str(path), read_only=True)
    try:
        return k.execute("SELECT * FROM tests WHERE card_id = ? ORDER BY tested_on", [card_id]).df().to_dict("records")
    finally:
        k.close()


def context_for(names: list[str], *, path: Path = PATH, max_chars: int = 3000) -> str:
    """A compact evidence summary for the given primitives - the context the System One model is given."""
    k = duckdb.connect(str(path), read_only=True)
    try:
        rows = k.execute("""SELECT c.name || ' [' || c.kind || ']', c.verdict, c.best_t, c.summary,
                                   string_agg(t.origin || ' h' || t.horizon || ': ' || coalesce(round(t.value_a, 1)::VARCHAR, '-')
                                              || '/' || coalesce(round(t.value_b, 1)::VARCHAR, '-'), '; ') AS ev
                            FROM cards c LEFT JOIN tests t USING (card_id)
                            WHERE c.name IN (SELECT UNNEST(?)) GROUP BY ALL""", [names]).fetchall()
    finally:
        k.close()
    text = "\n".join(f"- {n}: verdict {v}, best |t| {bt if bt is None else round(bt, 1)}; {s or ''} | {e or ''}"
                     for n, v, bt, s, e in rows)
    return re.sub(r"\s+\|\s+$", "", text)[:max_chars]
