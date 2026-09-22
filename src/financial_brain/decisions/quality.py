"""Decision quality, scored separately from profit and loss (C25).

A good decision can lose money and a bad one can make it. A system that learns only from
P&L therefore learns the wrong lesson roughly as often as the right one - it will punish a
well-reasoned trade that met a bad tape, and reward a reckless one that got lucky.

So process is scored on its own terms, from the record, before any outcome is known:

    evidence        both sides cited, and the citations still current
    thesis          a named mechanism and a named primary uncertainty
    falsifiability  invalidation conditions that something can actually check
    arithmetic      scenarios that sum to one, with a loss case, and a positive EV
    sizing          the position follows from the distance to invalidation
    process         the safety gate was consulted and its refusals respected

Each is a fraction of one, and the score is their mean. It says nothing about whether the
trade made money, which is the point: over a run of decisions, high quality with poor
returns means the edge is absent, while low quality with good returns means the system got
away with something.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import expected_value as ev


@dataclass
class Score:
    parts: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        return sum(self.parts.values()) / len(self.parts) if self.parts else 0.0

    def grade(self) -> str:
        s = self.score
        if s >= 0.85:
            return "sound"
        if s >= 0.6:
            return "workable"
        if s >= 0.4:
            return "thin"
        return "poor"

    def describe(self) -> str:
        weakest = sorted(self.parts.items(), key=lambda kv: kv[1])[:2]
        detail = ", ".join(f"{k} {v:.0%}" for k, v in weakest)
        return f"{self.score:.0%} ({self.grade()}); weakest: {detail}"


def _evidence(con, content: dict) -> tuple[float, list[str]]:
    from ..evidence import ledger
    support = content.get("supporting_evidence") or []
    against = content.get("contrary_evidence") or []
    if not support or not against:
        return 0.0, ["evidence cited on only one side"]
    live = 0
    for eid in support + against:
        try:
            live += ledger.current(con, eid) == eid
        except Exception:            # noqa: BLE001 - a missing id is a dead citation
            pass
    fraction = live / len(support + against)
    notes = [] if fraction == 1 else [f"{1 - fraction:.0%} of citations superseded"]
    breadth = min(1.0, (len(support) + len(against)) / 6)
    return (fraction * 0.6 + breadth * 0.4), notes


def _thesis(content: dict) -> tuple[float, list[str]]:
    thesis = (content.get("thesis") or "").strip()
    uncertainty = (content.get("primary_uncertainty") or "").strip()
    parts, notes = 0.0, []
    if len(thesis) >= 20:
        parts += 0.5
    else:
        notes.append("thesis is too short to say what the mechanism is")
    if len(uncertainty) >= 10:
        parts += 0.5
    else:
        notes.append("no named primary uncertainty")
    return parts, notes


def _falsifiability(content: dict) -> tuple[float, list[str]]:
    prose = content.get("invalidation_conditions") or []
    typed = content.get("invalidation_checks") or []
    if not prose and not typed:
        return 0.0, ["nothing would prove this wrong"]
    if not typed:
        return 0.3, ["invalidation is prose only; nothing re-checks it"]
    covered = min(1.0, len(typed) / max(len(prose), 1))
    return 0.6 + 0.4 * covered, ([] if covered >= 1 else
                                 [f"{len(prose) - len(typed)} condition(s) unmonitored"])


def _arithmetic(content: dict) -> tuple[float, list[str]]:
    try:
        assessment = ev.assess(content.get("scenarios") or {})
    except ev.EVError as e:
        return 0.0, [f"scenarios: {e}"]
    if not assessment.positive():
        return 0.4, [f"expected value is {assessment.expected_value:+.1%}"]
    rr = assessment.reward_to_risk or 0
    return (1.0 if rr >= 2 else 0.8), ([] if rr >= 2 else
                                       [f"reward to risk {rr:.1f} is below 2"])


def _sizing(content: dict) -> tuple[float, list[str]]:
    sizing = content.get("sizing") or {}
    if not sizing:
        return 0.0, ["no sizing at all"]
    entry, invalid = sizing.get("entry"), sizing.get("invalidation")
    if not entry or not invalid:
        return 0.3, ["sizing is a weight with no invalidation distance behind it"]
    try:
        ev.size(portfolio_inr=1_000_000.0, entry=float(entry),
                invalidation=float(invalid),
                risk_budget=float(sizing.get("risk_budget", ev.DEFAULT_RISK_BUDGET)))
    except ev.EVError as e:
        return 0.2, [f"sizing: {e}"]
    return 1.0, []


def _process(con, decision_id: str) -> tuple[float, list[str]]:
    events = [r[0] for r in con.execute("""SELECT note FROM decision_events
                                           WHERE decision_id = ? ORDER BY seq""",
                                        [decision_id]).fetchall()]
    text = " ".join(e or "" for e in events).lower()
    if "unsafe" in text or "constitution" in text:
        return 1.0, ["a refusal was recorded and respected"]
    return 0.8, []


def assess_decision(con, decision_id: str) -> Score:
    """Score one decision's process, without looking at what it earned."""
    row = con.execute("SELECT content FROM decisions WHERE decision_id = ?",
                      [decision_id]).fetchone()
    if not row:
        return Score(parts={}, notes=[f"unknown decision {decision_id}"])
    content = json.loads(row[0])
    score = Score()
    for name, (value, notes) in {
            "evidence": _evidence(con, content),
            "thesis": _thesis(content),
            "falsifiability": _falsifiability(content),
            "arithmetic": _arithmetic(content),
            "sizing": _sizing(content),
            "process": _process(con, decision_id)}.items():
        score.parts[name] = round(value, 3)
        score.notes += [f"{name}: {n}" for n in notes]
    return score


def against_outcomes(con) -> dict:
    """Quality against realised excess, over every closed trade.

    The comparison that matters: consistently high quality with poor returns says the
    edge is absent rather than the process broken, and the reverse says the system got
    away with something it should not repeat.
    """
    rows = con.execute("""SELECT p.decision_id, p.excess FROM paper_trades p
                          WHERE p.status = 'closed' AND p.excess IS NOT NULL""").fetchall()
    scored = [(assess_decision(con, did).score, excess) for did, excess in rows]
    if not scored:
        return {"n": 0}
    good = [e for q, e in scored if q >= 0.6]
    poor = [e for q, e in scored if q < 0.6]
    out = {"n": len(scored),
           "mean_quality": round(sum(q for q, _ in scored) / len(scored), 3),
           "well_made": {"n": len(good),
                         "mean_excess": (sum(good) / len(good)) if good else None},
           "poorly_made": {"n": len(poor),
                           "mean_excess": (sum(poor) / len(poor)) if poor else None}}
    if good and poor:
        out["quality_premium"] = (sum(good) / len(good)) - (sum(poor) / len(poor))
    return out
