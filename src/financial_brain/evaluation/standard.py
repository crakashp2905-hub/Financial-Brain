"""The one evidence standard (ADR-0004): the gates a strategy passes before it is called an edge, and the budget
that limits how fast trials are spent.

    G1 pre-registered      ``research.preregistry.audit`` reports the registration clean
    G2 net of costs        the caller passes a net series (the harness charges per-name costs)
    G3 replication         same sign, |t| >= 2, in 2016-2021 and in 2022-2026
    G4 null separation     min(t_a, t_b) beats the 95th percentile of the same statistic over random books with the
                           same number of names and the same turnover, from the same universe
    G5 out of sample       ``oos_monitor`` - >= 12 rebalances after the cutoff, net mean positive, inside the band

``judge`` returns ``CANDIDATE`` for G1-G4 and never ``CONFIRMED``: confirmation needs data that did not exist when
the test ran, and only ``oos_monitor`` can see it.

## The trial ledger

``docs/trials/ledger.jsonl`` - one JSON object per trial, appended, committed. It lives in git rather than the
database so every entry is dated by a commit, it cannot be rewritten without the history showing it, and recording
a trial never needs a write lock on the research database.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

SPLIT = pd.Timestamp("2022-01-01")
MIN_T = 2.0
NULL_QUANTILE = 0.95
NULL_DRAWS = 200
BUDGET_PER_QUARTER = 8
#: Owner-approved exceptions to the cap, by quarter (ADR-0004 amendments). Each one is a written decision.
BUDGET_OVERRIDES = {"2026Q4": 11}          # amendments 1-3: index mean reversion; published anomalies; intraday family
LEDGER = Path("docs") / "trials" / "ledger.jsonl"

CANDIDATE, FAIL = "CANDIDATE", "FAIL"


class StandardError(ValueError):
    pass


# ------------------------------------------------------------------------------------------------- G3

def tstat(x: pd.Series) -> float:
    x = pd.Series(x).dropna()
    if len(x) < 3 or x.std(ddof=1) == 0:
        return float("nan")
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x))))


def halves(s: pd.Series, split: pd.Timestamp = SPLIT) -> tuple[pd.Series, pd.Series]:
    s = pd.Series(s).dropna()
    s.index = pd.to_datetime(s.index)
    return s[s.index < split], s[s.index >= split]


def replication(s: pd.Series, *, direction: int = 1, split: pd.Timestamp = SPLIT) -> dict:
    """G3 on a period series of net returns (or net excess). ``direction`` -1 for a short or avoid signal."""
    a, b = halves(s, split)
    ta, tb = tstat(a) * direction, tstat(b) * direction
    stat = float(np.nanmin([ta, tb])) if np.isfinite(ta) and np.isfinite(tb) else float("nan")
    return {"n_a": len(a), "mean_a": float(a.mean()) if len(a) else np.nan, "t_a": ta,
            "n_b": len(b), "mean_b": float(b.mean()) if len(b) else np.nan, "t_b": tb,
            "stat": stat, "pass": bool(np.isfinite(stat) and stat >= MIN_T)}


# ------------------------------------------------------------------------------------------------- G4

def book_series(F: pd.DataFrame, cost: pd.DataFrame, bench: pd.Series, books: dict, weigh=None) -> pd.Series:
    """Net excess per rebalance of ``books`` (date -> names held to the next rebalance): the period return ``F``
    (dates x names), minus half each name's round trip on the weight changed, minus ``bench``. ``weigh(d, names)``
    returns weights summing to 1 (equal weight when omitted) - the null must be weighted the way the book is."""
    out, prev = {}, pd.Series(dtype=float)
    for d in sorted(books):
        names = [x for x in books[d] if np.isfinite(F.at[d, x])]
        if not names:
            continue
        w = weigh(d, names) if weigh else pd.Series(1.0 / len(names), index=names)
        g = float((w * F.loc[d, names]).sum())
        dw = w.sub(prev, fill_value=0).abs()
        c = float((dw * cost.loc[d].reindex(dw.index).fillna(cost.loc[d].median()) / 2).sum())
        out[d] = g - c - float(bench.get(d, 0.0))
        drift = w * (1 + F.loc[d, names].fillna(0))
        prev = drift / drift.sum()
    return pd.Series(out, dtype=float)


def random_books(books: dict, eligible: dict, *, seed: int) -> dict:
    """Random books matched to ``books``: the same number of names each date, and the same number replaced at each
    rebalance (so the same turnover), drawn from that date's ``eligible`` names."""
    rng = np.random.default_rng(seed)
    out, held, prev_real = {}, [], None
    for d in sorted(books):
        real = list(books[d])
        pool = list(eligible[d])
        if prev_real is None:
            held = list(rng.choice(pool, size=min(len(real), len(pool)), replace=False))
        else:
            keep_n = len(prev_real & set(real))
            ok = set(pool)
            held = [x for x in held if x in ok]
            rng.shuffle(held)
            held = held[:min(keep_n, len(held))]
            fresh = [x for x in pool if x not in set(held)]
            need = max(len(real) - len(held), 0)
            if need and fresh:
                held += list(rng.choice(fresh, size=min(need, len(fresh)), replace=False))
        out[d] = held
        prev_real = set(real)
    return out


def null_separation(real: pd.Series, F, cost, bench, books: dict, eligible: dict, *, direction: int = 1,
                    draws: int = NULL_DRAWS, seed: int = 20261013, weigh=None) -> dict:
    """G4: the replication statistic of the real series against random books of the same size and turnover."""
    stat = replication(real, direction=direction)["stat"]
    sims = []
    for i in range(draws):
        s = book_series(F, cost, bench, random_books(books, eligible, seed=seed + i), weigh)
        sims.append(replication(s, direction=direction)["stat"])
    sims = np.array([x for x in sims if np.isfinite(x)])
    q = float(np.quantile(sims, NULL_QUANTILE)) if len(sims) else float("nan")
    return {"stat": stat, "null_q95": q, "null_mean": float(sims.mean()) if len(sims) else np.nan,
            "draws": int(len(sims)), "pass": bool(np.isfinite(stat) and np.isfinite(q) and stat > q)}


# ------------------------------------------------------------------------------------------------- G1 + verdict

def registration(name: str, repo: Path = Path(".")) -> dict:
    """G1: the audit row for ``PRE-REGISTERED <name>.md``."""
    from ..research import preregistry as PR
    for r in PR.audit(repo):
        if r["prereg"] == f"PRE-REGISTERED {name}.md":
            return {"status": r["status"], "registered": r["registered"],
                    "pass": r["status"] in ("OK", "NO_RESULT_YET", "AMENDED_BEFORE_RESULT")}
    return {"status": "MISSING", "registered": None, "pass": False}


def judge(name: str, net: pd.Series, *, direction: int = 1, null: dict | None = None, repo: Path = Path(".")) -> dict:
    """G1-G4. ``null`` is :func:`null_separation`'s result (G4 fails without one: it is not optional)."""
    g1 = registration(name, repo)
    g3 = replication(net, direction=direction)
    g4 = null or {"pass": False, "why": "no null run"}
    gates = {"G1": g1, "G2": {"pass": True, "note": "net series supplied by the harness"}, "G3": g3, "G4": g4}
    failed = [k for k, v in gates.items() if not v["pass"]]
    return {"name": name, "verdict": FAIL if failed else CANDIDATE, "failed": failed, "gates": gates}


# ------------------------------------------------------------------------------------------------- budget

def _quarter(d: date) -> tuple[int, int]:
    return d.year, (d.month - 1) // 3 + 1


def ledger(path: Path = LEDGER) -> list[dict]:
    if not Path(path).exists():
        return []
    return [json.loads(ln) for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]


def check_budget(on: date, path: Path = LEDGER, budget: int | None = None) -> dict:
    q = _quarter(on)
    if budget is None:
        budget = BUDGET_OVERRIDES.get(f"{q[0]}Q{q[1]}", BUDGET_PER_QUARTER)
    used = [r for r in ledger(path) if r.get("kind") == "trial" and _quarter(date.fromisoformat(r["date"])) == q]
    return {"quarter": f"{q[0]}Q{q[1]}", "used": len(used), "budget": budget, "left": budget - len(used),
            "ok": len(used) < budget}


def record(entry: dict, path: Path = LEDGER, *, on: date | None = None) -> dict:
    """Append one trial. Refused past the quarter's budget, and refused for a name already recorded (a trial is
    spent once; a changed test is a new registration with a new name)."""
    on = on or date.today()
    if entry.get("kind", "trial") == "trial":
        b = check_budget(on, path)
        if not b["ok"]:
            raise StandardError(f"trial budget spent: {b['used']} of {b['budget']} in {b['quarter']}")
        if any(r.get("name") == entry["name"] and r.get("kind") == "trial" for r in ledger(path)):
            raise StandardError(f"{entry['name']!r} already has a recorded trial")
    row = {"kind": "trial", "date": on.isoformat(), "recorded_at": datetime.now(timezone.utc).isoformat(), **entry}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    return row
