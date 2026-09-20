"""Decision record and lifecycle (C17) - ARCHITECTURE.md §11.

Every recommendation is a structured, immutable record - never prose - moving through a
strict state machine:

    DRAFT -> EVIDENCE_VERIFIED -> RISK_REVIEWED -> PAPER_CANDIDATE -> HUMAN_APPROVED
          -> PROPOSED_TO_BROKER -> EXECUTED -> OUTCOME_MEASURED -> POSTMORTEM_COMPLETE
    (any non-terminal state) -> REJECTED | WITHDRAWN

The rules below are enforced here, in code, because "the model will behave" is not a
control:

* **Point-in-time evidence.** A decision references exactly one world-state version and
  may cite only evidence published at or before that state's as-of time. Citing what
  was not yet public is hindsight - the cheapest way to fool yourself in a backtest.
* **Current evidence.** A claim that has since been superseded cannot be cited.
* **Both sides.** Verification needs supporting *and* contrary evidence, a named primary
  uncertainty, and explicit invalidation conditions.
* **The constitution binds.** Risk review checks every machine-checkable rule of the
  owner's Investment Constitution (``constitution/rules.py``) as of the decision's
  world state; any violation blocks, and names the rule and the facts.
* **Only a human approves.** An ``agent:`` identity can never move a decision to
  HUMAN_APPROVED.
* **No broker path while execution is off.** It is off by default (Kite is read-only);
  PROPOSED_TO_BROKER is refused unless execution is explicitly enabled.
* **Append-only.** Content is frozen once evidence is verified; every transition is an
  event row. Nothing is updated or deleted.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone

from ..evidence import ledger

STATES = ["DRAFT", "EVIDENCE_VERIFIED", "RISK_REVIEWED", "PAPER_CANDIDATE",
          "HUMAN_APPROVED", "PROPOSED_TO_BROKER", "EXECUTED", "OUTCOME_MEASURED",
          "POSTMORTEM_COMPLETE"]
TERMINAL = {"POSTMORTEM_COMPLETE", "REJECTED", "WITHDRAWN"}
NEXT = {s: STATES[i + 1] for i, s in enumerate(STATES[:-1])}
ACTIONS = {"BUY", "ADD", "HOLD", "REDUCE", "EXIT", "AVOID", "WATCH"}


class DecisionError(ValueError):
    """A transition the rules forbid. The message says which rule."""


@dataclass
class Decision:
    isin: str
    action: str
    horizon_days: int
    thesis: str
    world_state_version: str
    supporting_evidence: list[str]
    contrary_evidence: list[str]
    primary_uncertainty: str
    invalidation_conditions: list[str]
    # Typed twins of the prose above, evaluated by decisions/monitor.py. A condition
    # with no typed check is reported unmonitored, never assumed satisfied.
    invalidation_checks: list[dict] = field(default_factory=list)
    universe: str = "NSE EQ"
    entry_logic: str = ""
    exit_logic: str = ""
    scenarios: dict = field(default_factory=dict)
    sizing: dict = field(default_factory=dict)      # e.g. {"weight": 0.03, "risk_budget": ...}
    portfolio_impact: str = ""
    author: str = "agent:unknown"

    def decision_id(self) -> str:
        body = json.dumps(ledger._canon(asdict(self)), sort_keys=True, separators=(",", ":"))
        return "dc_" + hashlib.sha256(body.encode()).hexdigest()[:24]


def _state(con, did: str) -> str:
    r = con.execute("""SELECT to_state FROM decision_events WHERE decision_id = ?
                       ORDER BY seq DESC LIMIT 1""", [did]).fetchone()
    if not r:
        raise DecisionError(f"unknown decision {did}")
    return r[0]


def _event(con, did: str, frm: str | None, to: str, actor: str, note: str) -> None:
    seq = con.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM decision_events "
                      "WHERE decision_id = ?", [did]).fetchone()[0]
    con.execute("""INSERT INTO decision_events (decision_id, seq, from_state, to_state, actor,
                   note, event_at) VALUES (?,?,?,?,?,?,?)""",
                [did, seq, frm, to, actor, note, datetime.now(timezone.utc)])


def draft(con, d: Decision) -> str:
    if d.action not in ACTIONS:
        raise DecisionError(f"action must be one of {sorted(ACTIONS)}")
    did = d.decision_id()
    if con.execute("SELECT 1 FROM decisions WHERE decision_id = ?", [did]).fetchone():
        return did
    con.execute("""INSERT INTO decisions (decision_id, isin, action, horizon_days,
                   world_state_version, content, author, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                [did, d.isin, d.action, d.horizon_days, d.world_state_version,
                 json.dumps(ledger._canon(asdict(d))), d.author, datetime.now(timezone.utc)])
    _event(con, did, None, "DRAFT", d.author, "drafted")
    return did


def content(con, did: str) -> dict:
    r = con.execute("SELECT content FROM decisions WHERE decision_id = ?", [did]).fetchone()
    if not r:
        raise DecisionError(f"unknown decision {did}")
    return json.loads(r[0])


def _verify_evidence(con, c: dict) -> None:
    ws = con.execute("SELECT content FROM world_states WHERE version_id = ?",
                     [c["world_state_version"]]).fetchone()
    if not ws:
        raise DecisionError(f"world state {c['world_state_version']} does not exist")
    as_of = datetime.fromisoformat(str(json.loads(ws[0])["as_of"]))
    if not c["supporting_evidence"] or not c["contrary_evidence"]:
        raise DecisionError("needs both supporting and contrary evidence")
    if not c["primary_uncertainty"].strip():
        raise DecisionError("needs a named primary uncertainty")
    if not c["invalidation_conditions"]:
        raise DecisionError("needs explicit invalidation conditions")
    for eid in c["supporting_evidence"] + c["contrary_evidence"]:
        row = con.execute("SELECT published_at, as_of FROM evidence WHERE evidence_id = ?",
                          [eid]).fetchone()
        if not row:
            raise DecisionError(f"evidence {eid} does not exist")
        known_at = row[0] or row[1]
        if known_at and known_at > as_of:
            raise DecisionError(f"evidence {eid} was published {known_at}, after the world "
                                f"state's as-of {as_of} - that is hindsight")
        if ledger.current(con, eid) != eid:
            raise DecisionError(f"evidence {eid} has been superseded by "
                                f"{ledger.current(con, eid)}")


def advance(con, did: str, *, actor: str, note: str = "",
            execution_enabled: bool = False, constitution: dict | None = None) -> str:
    """Move a decision one step forward, enforcing the rule for that step."""
    frm = _state(con, did)
    if frm in TERMINAL:
        raise DecisionError(f"{did} is {frm}; nothing follows")
    to = NEXT[frm]
    c = content(con, did)
    if to == "EVIDENCE_VERIFIED":
        _verify_evidence(con, c)
    elif to == "RISK_REVIEWED":
        if not c.get("sizing"):
            raise DecisionError("risk review needs sizing / a risk budget")
        from ..config import load
        from ..constitution import rules
        book = constitution if constitution is not None else rules.load(load().data_root)
        broken = rules.check(con, c, book)
        if broken:
            raise DecisionError("constitution: " + "; ".join(
                f"{v['rule']} ({v['fact']})" for v in broken))
    elif to == "HUMAN_APPROVED" and (not actor or actor.startswith("agent:")):
        raise DecisionError("only a human can approve; agents may propose, never approve")
    elif to == "PROPOSED_TO_BROKER" and not execution_enabled:
        raise DecisionError("execution is disabled (Kite is read-only); paper only")
    elif to == "OUTCOME_MEASURED":
        created = con.execute("SELECT created_at FROM decisions WHERE decision_id = ?",
                              [did]).fetchone()[0]
        if (datetime.now(timezone.utc) - created).days < c["horizon_days"]:
            raise DecisionError("the outcome window has not elapsed")
    _event(con, did, frm, to, actor, note)
    ledger.use(con, c["supporting_evidence"] + c["contrary_evidence"],
               used_by_kind="decision", used_by_id=did)
    return to


def close(con, did: str, *, to: str, actor: str, note: str) -> str:
    """Reject or withdraw a decision - with a reason, which is kept."""
    if to not in ("REJECTED", "WITHDRAWN"):
        raise DecisionError("close to REJECTED or WITHDRAWN")
    if not note.strip():
        raise DecisionError("closing a decision needs a reason")
    frm = _state(con, did)
    if frm in TERMINAL:
        raise DecisionError(f"{did} is already {frm}")
    _event(con, did, frm, to, actor, note)
    return to


def history(con, did: str) -> list[dict]:
    return [dict(zip(["seq", "from_state", "to_state", "actor", "note", "at"], r))
            for r in con.execute("""SELECT seq, from_state, to_state, actor, note, event_at
                                    FROM decision_events WHERE decision_id = ?
                                    ORDER BY seq""", [did]).fetchall()]
