"""From an approved rule to a paper cohort that actually avoids the mistake - and the A/B that shows whether it did.

An ACTIVE skip rule (``shadow.load_rules``: approved by a person, approval current, pre-registration intact) becomes entry filters
on the built-in strategies in a **separate paper cohort**, ``live-v2``, with its own ledger and its own run row, beside the
unchanged live-v1:

    fb paper-intraday --v2            # live, 09:05 (scripts/paper_intraday_v2.cmd)
    fb paper-intraday --v2 --replay D # a past day, labelled replay

The filters are the engine's own (``DayEngine._filtered_builtin``): the built-in signal runs as always, the filter sees the
decision-time context (the same dimensions, computed by the same code, as the loss review) and may decline the entry. A declined
entry ends the strategy for that name today, exactly as a taken one does, so what remains is what v1 would have traded.
``tests/test_learning_conformance.py`` runs v1 and v2 over the same simulated sessions and requires exactly that.

``ab`` is the verdict that matters: v1 and v2 on the *same days*, which entries only v1 took (the declined ones), which only v2
took (exposure freed by a skip, the effect the shadow cannot see), and the paired per-day difference in net.
"""
from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from ..paper import intraday as I
from . import features as F
from . import shadow as SH

COHORT = "live-v2"


def active_rules(path: Path = SH.RULES_FILE, **kw) -> list[SH.SkipRule]:
    return [r for r in SH.load_rules(path, **kw) if r.effective_status == "ACTIVE"]


def _delegated(*_a, **_k):
    raise I.EngineError("a live-v2 rule delegates to its built-in strategy; the engine never calls this")


def _predicate(rules: list[SH.SkipRule]):
    def skip(ctx: dict) -> str | None:
        for r in rules:
            if F.value_of(r.dimension, ctx) == r.value:
                return f"skip rule {r.id}: {r.pattern}"
        return None
    return skip


def build_engine_rules(active: list[SH.SkipRule]) -> dict[str, I.StrategyRule]:
    """One filtered rule per built-in strategy that has at least one active skip rule, versioned by the rules in force."""
    out = {}
    for strat in I.STRATEGIES:
        mine = sorted((r for r in active if r.strategy == strat), key=lambda r: r.id)
        if mine:
            ver = hashlib.sha1("|".join(f"{r.id}:{r.cell}" for r in mine).encode()).hexdigest()[:8]
            out[strat] = I.StrategyRule(signal=_delegated, version=f"{strat}-skip-{ver}", base=strat, skip=_predicate(mine))
    return out


def uses_day_context(active: list[SH.SkipRule]) -> bool:
    return any(r.dimension in ("regime_prev", "vix_bucket") for r in active)


def day_context(day: str, research_db: Path = Path("data") / "financial_brain.duckdb") -> dict:
    """The previous session's regime label and India VIX, read-only from the research database."""
    con = duckdb.connect(str(research_db), read_only=True)
    try:
        r = con.execute("""SELECT regime, vix FROM market_regime WHERE business_date < CAST(? AS DATE) AND version = 'v2'
                           ORDER BY business_date DESC LIMIT 1""", [day]).fetchone()
    finally:
        con.close()
    return {"regime_prev": r[0], "vix": r[1]} if r else {}


def cohort_args(active: list[SH.SkipRule]) -> dict:
    """The keyword arguments for ``intraday_runner.run_live`` / ``run_replay`` that run the live-v2 cohort."""
    rules = build_engine_rules(active)
    return {"rules": rules, "cohort": COHORT, "strategies": I.STRATEGIES,
            "day_context_fn": day_context if uses_day_context(active) else None}


# ------------------------------------------------------------------------------------------------ the A/B

def v2_ledgers(data_dir: Path = Path("data")) -> list[Path]:
    return sorted(data_dir.glob(f"paper_live-{COHORT}-*.duckdb"))


def _closed(path: Path, cohort: bool) -> pd.DataFrame:
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute("""SELECT day, strategy, symbol, side, qty, entry_px, net_pnl FROM trades
                              WHERE exit_ts IS NOT NULL AND mode = 'live'""").df()
    finally:
        con.close()


def ab(v1_path: Path, v2_paths: list[Path]) -> dict:
    """v1 against v2 on the sessions both completed. Returns the per-day paired difference and which entries diverged."""
    a = _closed(v1_path, False)
    b = pd.concat([_closed(p, True) for p in v2_paths], ignore_index=True) if v2_paths else pd.DataFrame()
    if a.empty or b.empty:
        return {"days": 0, "note": "no common sessions yet"}
    days = sorted(set(a["day"]) & set(b["day"]))
    if not days:
        return {"days": 0, "note": "no common sessions yet"}
    a, b = a[a["day"].isin(days)], b[b["day"].isin(days)]
    key = ["day", "strategy", "symbol"]
    ka, kb = a.set_index(key).index, b.set_index(key).index
    only_v1 = a[~ka.isin(kb)]
    only_v2 = b[~kb.isin(ka)]
    d1, d2 = a.groupby("day")["net_pnl"].sum(), b.groupby("day")["net_pnl"].sum()
    diff = (d2.reindex(days).fillna(0) - d1.reindex(days).fillna(0))
    t = float(diff.mean() / (diff.std(ddof=1) / np.sqrt(len(diff)))) if len(diff) > 2 and diff.std(ddof=1) > 0 else None
    return {"days": len(days), "v1_net_inr": float(a["net_pnl"].sum()), "v2_net_inr": float(b["net_pnl"].sum()),
            "diff_inr": float(diff.sum()), "diff_per_day_inr": float(diff.mean()), "paired_t": t,
            "declined": int(len(only_v1)), "declined_net_inr": float(only_v1["net_pnl"].sum()),
            "only_v2": int(len(only_v2)), "only_v2_net_inr": float(only_v2["net_pnl"].sum()),
            "note": "declined = entries only v1 took; only_v2 = entries only v2 took (exposure a skip freed)"}


def render_ab(r: dict) -> str:
    if not r.get("days"):
        return f"A/B: {r.get('note', 'no data')}"
    t = f", paired t {r['paired_t']:+.2f}" if r.get("paired_t") is not None else ""
    return (f"A/B over {r['days']} common sessions: v1 Rs {r['v1_net_inr']:,.0f}, v2 Rs {r['v2_net_inr']:,.0f} "
            f"(v2 - v1 {r['diff_inr']:+,.0f}, {r['diff_per_day_inr']:+,.0f} a day{t}). v2 declined {r['declined']} entries that "
            f"made Rs {r['declined_net_inr']:,.0f} for v1, and took {r['only_v2']} that v1 did not (Rs {r['only_v2_net_inr']:,.0f}).")


def today() -> str:
    return date.today().isoformat()
