"""Model router (ADR-0002): the cheapest model that is *measured* good enough, first.

For a task, the route is built from the model benchmark, not from reputation:

1. candidates are the models with a ``model_bench`` row for the task **and a calibrated
   threshold** (their confidence carries information), plus the task's deterministic
   rules if any;
2. they are ordered by tier (0 rules, 1 tiny/encoder, 2 local 7-14B, 3 cheap cloud,
   4 frontier), then by measured latency;
3. each is asked in turn; the first answer whose confidence reaches that model's own
   threshold is accepted. Otherwise the router escalates.
4. If nothing clears its bar, the most capable model's answer is returned marked
   ``uncertain`` - never silently promoted to a fact. Cloud tiers join the route only
   when the owner has enabled them (``FB_LLM_ENABLED=1``).

Every model call is recorded in ``llm_calls`` (task, tier, latency, cost; the input is
hashed, not stored).
"""
from __future__ import annotations

import hashlib
import json
import os
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..evaluation import models as bench
from . import system1

REGISTRY = Path(__file__).with_name("models.toml")
BUILTIN_TIER = {"rules": 0, "finbert": 1}


def registry() -> dict[str, dict]:
    return {m["name"]: m for m in tomllib.loads(REGISTRY.read_text("utf-8"))["model"]}


def tier(model: str) -> int:
    if model in BUILTIN_TIER:
        return BUILTIN_TIER[model]
    return registry().get(model, {}).get("tier", 2)


@dataclass
class Routed:
    decision: system1.Decision
    accepted: bool                        # False = nothing cleared its threshold
    route: list[dict] = field(default_factory=list)


def plan(con, task: str, *, optimised: bool = True) -> list[dict]:
    """The route for a task: the chain chosen by ``fb models route`` if one is stored,
    else every calibrated model, cheapest first."""
    cloud_ok = os.environ.get("FB_LLM_ENABLED") == "1"
    if optimised:
        r = con.execute("""SELECT steps FROM model_routes WHERE task = ?
                           ORDER BY chosen_at DESC LIMIT 1""", [task]).fetchone()
        if r:
            return [s for s in json.loads(r[0]) if s["tier"] < 3 or cloud_ok]
    rows = con.execute("""
        SELECT model, threshold, latency_ms, macro_f1, detail FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY model ORDER BY run_at DESC) rk
            FROM model_bench WHERE task = ?) WHERE rk = 1 AND threshold IS NOT NULL""",
                       [task]).fetchall()
    # Only models measured under the prompt we actually send are candidates. Without
    # this the optimiser happily picks a model whose numbers came from an older prompt,
    # and the route is then rejected as stale the moment anything tries to use it.
    want = system1.prompt_version()
    steps = [{"model": m, "threshold": t, "latency_ms": lat, "macro_f1": f1, "tier": tier(m),
              "thresholds": json.loads(det or "{}").get("thresholds"),
              "prompt_version": json.loads(det or "{}").get("prompt_version")}
             for m, t, lat, f1, det in rows
             if (tier(m) < 3 or cloud_ok)
             and json.loads(det or "{}").get("prompt_version") == want]
    return sorted(steps, key=lambda s: (s["tier"], s["latency_ms"] or 0))


def route_is_current(con, task: str) -> tuple[bool, str]:
    """Was the stored route measured under the prompt we now send?"""
    r = con.execute("""SELECT steps FROM model_routes WHERE task = ?
                       ORDER BY chosen_at DESC LIMIT 1""", [task]).fetchone()
    if not r:
        return False, "no stored route"
    want = system1.prompt_version()
    for step in json.loads(r[0]):
        got = step.get("prompt_version")
        if got != want:
            return False, (f"{step['model']} was calibrated under prompt {got or 'unknown'}, "
                           f"the deployed prompt is {want}: re-run "
                           f"`fb models bench --task {task}`")
    return True, "current"


def has_route(con, task: str) -> bool:
    """Is there a stored, verified route for this task? Callers use it to decide whether
    a task may be answered at all, rather than borrowing another task's calibration."""
    return route_is_current(con, task)[0]


def choose(con, task: str, *, budget_ms: float = 4000, verify_on: str | None = None,
           max_accepted_error: float = 0.10) -> dict:
    """Pick and store a route (replayed, no model calls). With ``verify_on`` the route
    must hold up on a set that had no part in fitting its thresholds; if none does,
    nothing is stored and the router declines the task."""
    best = bench.optimise_route(con, task, budget_ms=budget_ms, verify_on=verify_on,
                                max_accepted_error=max_accepted_error)
    if best.get("chain"):
        con.execute("""INSERT INTO model_routes (task, steps, simulated, budget_ms, chosen_at)
                       VALUES (?,?,?,?,?)""",
                    [task, json.dumps(best["chain"]),
                     json.dumps({"tuning": best["simulated"], "verified": best.get("verified")}),
                     budget_ms, datetime.now(timezone.utc)])
    return best


def _record(con, task: str, state: str, d: system1.Decision, t: int) -> None:
    price = registry().get(d.model, {})
    cost = d.input_tokens * price.get("price_in", 0.0) / 1e6
    con.execute("""INSERT INTO llm_calls (called_at, purpose, model, prompt_sha256,
                   output_sha256, input_tokens, output_tokens, evidence, task, tier,
                   latency_ms, cost_usd) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                [datetime.now(timezone.utc), "classify", d.model,
                 hashlib.sha256(state.encode()).hexdigest(),
                 hashlib.sha256(d.label.encode()).hexdigest(), d.input_tokens, 1, [],
                 task, t, d.latency_ms, cost])


def decide(con, task: str, row: dict, *, steps: list[dict] | None = None) -> Routed:
    """``row`` is a benchmark-shaped input: {"state": text, ...task extras}."""
    steps = plan(con, task) if steps is None else steps
    if not steps:
        raise RuntimeError(f"no benchmarked model clears its bar for {task!r}; run "
                           f"`fb models bench --task {task}` first")
    route, last = [], None
    for s in steps:
        d = bench.decider(task, s["model"])(row)
        if s["model"] not in BUILTIN_TIER:
            _record(con, task, row["state"], d, s["tier"])
        ok = bench.accepts(s, d.label, d.confidence)
        route.append({"model": s["model"], "label": d.label,
                      "confidence": round(d.confidence, 4), "accepted": ok})
        last = d
        if ok:
            return Routed(d, True, route)
    return Routed(last, False, route)
