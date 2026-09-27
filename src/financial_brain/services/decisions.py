"""The decision path, and the two things it will never do.

**It will never reach a broker.** ``PROPOSED_TO_BROKER`` requires ``execution_enabled``, which is
off, and Kite is configured read-only with no order endpoint anywhere in ``providers/``. This
service does not take an ``execution_enabled`` argument at all, so it cannot be flipped by a
caller - enabling execution is a code change, reviewed, not a request parameter.

**It will never approve.** ``HUMAN_APPROVED`` refuses any actor whose identity starts with
``agent:``, and this service refuses to pass an actor through to that transition regardless. An
API endpoint that could approve a decision would make the human approval gate a formality, since
whoever holds the API token becomes the human.
"""
from __future__ import annotations

from datetime import date

from ..decisions import adversary as adv
from ..decisions import compile as dcompile
from ..decisions import quality, record
from . import BAD_REQUEST, FORBIDDEN, NOT_FOUND, ServiceError

#: Transitions this layer refuses to perform whatever the caller says.
NEVER_VIA_SERVICE = {"HUMAN_APPROVED", "PROPOSED_TO_BROKER"}


def compile_one(con, *, isin: str, as_of: date, claims: list[dict],
                scenarios: dict | None, entry: float | None,
                invalidation: float | None, capital_inr: float,
                gate_decision: dict | None = None,
                horizon_days: int = 90) -> dict:
    """Resolve typed claims into one action by the compiler's fixed precedence.

    ``claims`` arrive as dicts so a caller need not import the dataclass; they are validated by
    the compiler, which refuses a strength outside [0, 1] rather than clamping it.
    """
    try:
        parsed = [dcompile.Claim(
            source=str(c["source"]), direction=int(c["direction"]),
            strength=float(c["strength"]), evidence=list(c.get("evidence") or []),
            rationale=str(c.get("rationale") or ""),
            horizon_days=c.get("horizon_days")) for c in claims]
    except (KeyError, TypeError, ValueError) as exc:
        raise ServiceError(BAD_REQUEST,
                           f"each claim needs source, direction and strength: {exc}") from exc
    try:
        out = dcompile.compile_decision(
            con, isin=isin, as_of=as_of, claims=parsed, scenarios=scenarios,
            entry=entry, invalidation=invalidation, capital_inr=capital_inr,
            gate_decision=gate_decision, horizon_days=horizon_days)
    except dcompile.CompileError as exc:
        raise ServiceError(BAD_REQUEST, str(exc)) from exc
    return out.as_dict()


def get(con, decision_id: str) -> dict:
    """One decision: its content, its state, its history and its adversary attempts."""
    try:
        content = record.content(con, decision_id)
    except record.DecisionError as exc:
        raise ServiceError(NOT_FOUND, str(exc)) from exc
    hist = record.history(con, decision_id)
    return {
        "decision_id": decision_id,
        "state": hist[-1]["to_state"] if hist else None,
        "content": content,
        "history": hist,
        "adversary_attempts": adv.attempts(con, decision_id),
        "is_no_trade": content.get("action") in record.NO_TRADE,
    }


def listing(con, *, state: str | None = None, action: str | None = None,
            limit: int = 50) -> list[dict]:
    rows = con.execute("""
        WITH latest AS (
            SELECT decision_id, to_state,
                   ROW_NUMBER() OVER (PARTITION BY decision_id ORDER BY seq DESC) AS rn
            FROM decision_events
        )
        SELECT d.decision_id, d.isin, d.action, d.horizon_days, d.author, d.created_at,
               l.to_state
        FROM decisions d LEFT JOIN latest l
          ON l.decision_id = d.decision_id AND l.rn = 1
        WHERE (? IS NULL OR l.to_state = ?) AND (? IS NULL OR d.action = ?)
        ORDER BY d.created_at DESC LIMIT ?
    """, [state, state, action, action, limit]).fetchall()
    return [{"decision_id": r[0], "isin": r[1], "action": r[2], "horizon_days": r[3],
             "author": r[4], "created_at": r[5], "state": r[6],
             "is_no_trade": r[2] in record.NO_TRADE} for r in rows]


def challenge(con, *, decision_id: str, mechanism: str, observable: str,
              evidence: list[str], probability: float = 0.0,
              author: str = "agent:adversary") -> dict:
    """Submit a thesis challenge. Recorded whether it passes, because a decision whose
    adversary needed four attempts is a different object from one whose adversary got it right."""
    try:
        content = record.content(con, decision_id)
    except record.DecisionError as exc:
        raise ServiceError(NOT_FOUND, str(exc)) from exc
    r = adv.gate(con, decision_id,
                 adv.Challenge(mechanism=mechanism, observable=observable,
                               evidence=evidence, probability=probability, author=author),
                 content=content)
    return {"decision_id": decision_id, "passed": r.passed,
            "failures": r.failures, "measured": r.measured,
            "attempts": len(adv.attempts(con, decision_id))}


def advance(con, *, decision_id: str, actor: str, note: str = "") -> dict:
    """Move a decision one step, refusing the two transitions this layer must never make.

    The refusal is here and not only in ``record.advance`` because defence in depth is cheap and
    the failure would be expensive: ``record`` already blocks an ``agent:`` actor from approving
    and blocks the broker path while execution is off, and this adds that no *caller of a service*
    can attempt either regardless of the identity it claims.
    """
    if not actor or actor.startswith("agent:"):
        raise ServiceError(FORBIDDEN, "an actor identity is required and may not be an agent")
    try:
        current = record.history(con, decision_id)
    except record.DecisionError as exc:
        raise ServiceError(NOT_FOUND, str(exc)) from exc
    if not current:
        raise ServiceError(NOT_FOUND, f"no history for {decision_id}")
    state = current[-1]["to_state"]
    nxt = record.NEXT.get(state)
    if nxt in NEVER_VIA_SERVICE:
        raise ServiceError(
            FORBIDDEN,
            f"{state} -> {nxt} is not available through a service. Human approval must happen "
            f"where a human is, and the broker path is disabled in code, not by a parameter.",
            state=state, requested=nxt)
    try:
        to = record.advance(con, decision_id, actor=actor, note=note)
    except record.DecisionError as exc:
        raise ServiceError(BAD_REQUEST, str(exc), state=state) from exc
    return {"decision_id": decision_id, "from_state": state, "to_state": to}


def score_quality(con, decision_id: str) -> dict:
    """Process quality, scored from the record and independently of profit and loss."""
    try:
        s = quality.assess_decision(con, decision_id)
    except Exception as exc:                                   # noqa: BLE001
        raise ServiceError(NOT_FOUND, str(exc)) from exc
    return {"decision_id": decision_id,
            "score": getattr(s, "score", None),
            "components": {k: v for k, v in vars(s).items() if k != "decision_id"},
            "note": "scored from the record before any outcome is known; P&L is not an input"}
