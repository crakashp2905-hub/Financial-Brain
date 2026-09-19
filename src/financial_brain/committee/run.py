"""Investment committee (C16) on the tiered-model router - TradingAgents, made cheap.

TradingAgents (Tauric Research, Apache-2.0) runs analysts -> bull/bear debate -> trader
-> risk review with a frontier LLM in every seat. Here the seats are filled by cost:

1. **Dossier** (Tier 0) - facts only, each a cited ledger claim (``dossier.py``).
2. **Analysts** (local model, typed) - price, filings and governance analysts each see
   only their slice of the dossier and return bullish / bearish / neutral with a
   probability. This stance task is *not yet benchmarked*; stances are advisory inputs
   and are recorded as such.
3. **Debate** (local model, structured) - a bull and a bear each give up to three points,
   every point citing dossier fact ids. A point citing an id the dossier does not hold is
   dropped: the committee cannot invent evidence.
4. **Chair** (deterministic by default) - weighs the stances, refuses BUY when the
   constitution would, and drafts a Decision (``decisions/record.py``, state DRAFT,
   author ``agent:committee``) whose supporting and contrary evidence are the bull's and
   bear's cited facts. A frontier chair may later rewrite the thesis *text* when the owner
   enables cloud calls; the evidence ids and the action stay deterministic.

Nothing here approves anything: the lifecycle's rules (both sides, point-in-time
evidence, constitution at risk review, human approval) still apply to the draft.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from ..decisions import record as dr
from ..llm import backends, system1
from . import dossier as dz

MODEL = "llama3.1:8b"           # best measured local model on filing sentiment (F1 0.82)
STANCES = ["bullish", "bearish", "neutral"]
ANALYSTS = {"price": {"price", "market"}, "filings": {"filing"},
            "governance": {"governance", "constitution"}}
POINTS_SCHEMA = {"type": "object", "properties": {"points": {"type": "array", "maxItems": 3,
                 "items": {"type": "object", "properties": {
                     "claim": {"type": "string"},
                     "fact_ids": {"type": "array", "items": {"type": "string"}}},
                     "required": ["claim", "fact_ids"]}}}, "required": ["points"]}


@dataclass
class Result:
    isin: str
    world_state_version: str
    stances: dict = field(default_factory=dict)
    bull: list = field(default_factory=list)
    bear: list = field(default_factory=list)
    dropped_points: int = 0
    action: str = "WATCH"
    decision_id: str | None = None
    model: str = MODEL


def analyse(doss: dz.Dossier, role: str, kinds: set[str], model: str,
            decide=system1.ollama_decide) -> dict:
    facts = doss.render(kinds)
    if not facts:
        return {"stance": "neutral", "confidence": 0.0, "note": "no facts in this slice"}
    d = decide(model, f"You are the {role} analyst on an investment committee for an "
               f"Indian listed company ({doss.company}). From these facts only, is the "
               f"{role} picture for a long-term shareholder bullish, bearish or neutral?",
               facts, STANCES)
    return {"stance": d.label, "confidence": round(d.confidence, 4)}


def argue(doss: dz.Dossier, side: str, model: str, generate=backends.ollama) -> tuple[list, int]:
    c = generate(model, "You argue one side of an investment committee debate. Use only "
                 "the facts given; cite each point with the fact ids in square brackets "
                 "exactly as shown. Return JSON.",
                 f"Company: {doss.company}\nFacts:\n{doss.render()}\n\nGive up to three "
                 f"{'reasons to BUY' if side == 'bull' else 'reasons NOT to buy'}, each "
                 "citing the fact ids that support it.", schema=POINTS_SCHEMA,
                 max_tokens=500)
    points = (c.parsed or {}).get("points", []) if c.parsed else []
    valid, dropped = [], 0
    for p in points:
        ids = [i.strip("[] ") for i in p.get("fact_ids", [])]
        ids = [i for i in ids if i in doss.ids()]
        if ids and p.get("claim"):
            valid.append({"claim": p["claim"][:300], "fact_ids": ids})
        else:
            dropped += 1
    return valid, dropped


def chair(doss: dz.Dossier, res: Result, constitution_ok: bool) -> str:
    score = sum((1 if s["stance"] == "bullish" else -1 if s["stance"] == "bearish" else 0)
                * s["confidence"] for s in res.stances.values())
    if score <= -0.5:
        return "AVOID"
    if score >= 0.5 and res.bull and res.bear and constitution_ok:
        return "BUY"
    return "WATCH"


def convene(con, isin: str, world_state_version: str, *, model: str = MODEL,
            constitution: dict | None = None, decide=system1.ollama_decide,
            generate=backends.ollama) -> Result:
    doss = dz.build(con, isin, world_state_version, constitution=constitution)
    res = Result(isin, world_state_version, model=model)
    for role, kinds in ANALYSTS.items():
        res.stances[role] = analyse(doss, role, kinds, model, decide)
    res.bull, d1 = argue(doss, "bull", model, generate)
    res.bear, d2 = argue(doss, "bear", model, generate)
    res.dropped_points = d1 + d2
    const_fact = next((f for f in doss.facts if f.kind == "constitution"), None)
    ok = bool(const_fact and const_fact.text.startswith("a BUY would pass"))
    res.action = chair(doss, res, ok)
    if res.bull and res.bear:
        sup = list(dict.fromkeys(i for p in res.bull for i in p["fact_ids"]))
        con_ = list(dict.fromkeys(i for p in res.bear for i in p["fact_ids"]))
        res.decision_id = dr.draft(con, dr.Decision(
            isin=isin, action=res.action, horizon_days=90,
            thesis=res.bull[0]["claim"] if res.action == "BUY" else
            f"{res.action}: " + (res.bear[0]["claim"] if res.action == "AVOID"
                                 else "evidence is mixed"),
            world_state_version=world_state_version, supporting_evidence=sup,
            contrary_evidence=con_, primary_uncertainty=res.bear[0]["claim"],
            invalidation_conditions=[f"if: {p['claim']}" for p in res.bear],
            sizing={"weight": 0.03} if res.action == "BUY" else {},
            author="agent:committee"))
    con.execute("""INSERT INTO committee_runs (isin, world_state_version, model, result,
                   decision_id, run_at) VALUES (?,?,?,?,?,?)""",
                [isin, world_state_version, model, json.dumps(asdict(res)),
                 res.decision_id, datetime.now(timezone.utc)])
    return res
