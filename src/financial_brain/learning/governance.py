"""Who decides, and what they decided: human-only approval, bound to exactly what was approved.

The loop can nominate, confirm, draft and measure. It cannot activate anything. A skip rule reaches the paper v2 cohort only
through an approval that is

* **human** - ``approvals.validate_approval`` refuses actor names that look like software, and ``approve`` below runs only at an
  interactive terminal with a typed confirmation, so a script, a scheduled job or an agent calling the CLI has no way to answer it;
* **scoped** - it carries the hash of the rule as written (id, lesson, strategy, dimension, value, pre-registration, start date), so
  editing the rule afterwards invalidates it and the rule returns to shadow;
* **expiring** - 180 days by default, after which the rule silently returns to shadow until someone looks at it again;
* **evidence-gated** - refused unless the latest shadow verdict is PASSING.

Everything is also written to the lessons ledger with the approver's name, so the record of who put a rule in force survives the
rules file being edited.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..forward_edge.approvals import Approval, ApprovalError, is_agent_actor, validate_approval
from . import shadow as SH

KIND = "STRATEGY_ENROLLMENT"
DEFAULT_DAYS = 180
ADR = "ADR-0004"
DRAFT_DIR = Path("brain") / "07-Research"


class GovernanceError(ValueError):
    pass


def scope_sha(rule) -> str:
    return hashlib.sha256(json.dumps(SH.scope_definition(rule), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _dt(x: str) -> datetime:
    d = datetime.fromisoformat(x)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def approval_problem(rule, now: datetime) -> str | None:
    """Why a rule's approval does not currently hold, or None if it does."""
    a = rule.approval
    if not a:
        return "marked ACTIVE but no approval is recorded: running in shadow"
    try:
        ap = Approval(kind=a["kind"], subject=a["subject"], scope_sha256=a["scope_sha256"], actor_id=a["actor_id"],
                      actor_type=a["actor_type"], approved_at_utc=_dt(a["approved_at_utc"]),
                      expires_at_utc=_dt(a["expires_at_utc"]), adr_ref=a.get("adr_ref"))
        validate_approval(ap, kind=KIND, subject=rule.id, scope_sha256=scope_sha(rule), now=now)
        if not ap.adr_ref:
            raise ApprovalError("the approval does not reference the governing ADR")
    except (KeyError, ValueError) as e:
        return f"approval does not hold ({e}): running in shadow"
    return None


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _find(doc: dict, rule_id: str) -> dict:
    for r in doc.get("rules", []):
        if r.get("id") == rule_id:
            return r
    raise GovernanceError(f"no rule {rule_id} in the rules file")


def _need_person(interactive: bool | None) -> None:
    ok = sys.stdin.isatty() if interactive is None else interactive
    if not ok:
        raise GovernanceError("this needs a person at a terminal: input is not interactive, so it was refused")


def _latest_shadow(store, rule: dict) -> dict | None:
    rows = [e for e in store.events(rule.get("lesson") or rule["id"])
            if e["kind"] == "SHADOW_RESULT" and e["payload"].get("rule") == rule["id"]]
    return rows[-1]["payload"] if rows else None


def next_rule_id(path: Path = SH.RULES_FILE) -> str:
    ids = [int(r["id"].split("-")[1]) for r in (_read(path).get("rules", []) if path.exists() else [])
           if r.get("id", "").startswith("SK-") and r["id"].split("-")[1].isdigit()]
    return f"SK-{(max(ids) + 1 if ids else 1):04d}"


# ------------------------------------------------------------------------------------------------ card and draft

def card(lesson: dict, shadow: dict | None = None) -> str:
    n, d = lesson.get("nomination") or {}, lesson.get("decision") or {}
    lines = [f"Lesson {lesson['id']}   stage {lesson['stage']}", f"  claim: {n.get('strategy')} trades with {n.get('dimension')} = "
             f"{n.get('value')} are worse than the strategy's other trades the same day, and lose.",
             f"  found:     {n.get('trades')} trades over {n.get('days')} sessions to {n.get('d0')}: {n.get('mean_bp', 0):+.1f} bp a trade, "
             f"{n.get('contrast_bp', 0):+.1f} bp worse than the rest (p {n.get('p', 0):.4f}, q {n.get('q', 0):.3f}, "
             f"{n.get('family_size')} cells in its family)"]
    if d:
        lines.append(f"  decided:   {d.get('kind')} on {d.get('trades')} fresh trades over {d.get('sessions_after')} sessions: "
                     f"{d.get('mean_bp', 0):+.1f} bp, {d.get('contrast_bp', 0):+.1f} bp worse than the rest (p {d.get('p', 0):.4f}, "
                     f"needed {d.get('alpha', 0):.4f})")
    if shadow:
        lines.append(f"  shadow:    {shadow['status']} - {shadow['skipped_trades']} trades would have been skipped over {shadow['days']} "
                     f"sessions, v1 Rs {shadow['v1_net_inr']:,.0f} -> v2 Rs {shadow['v2_net_inr']:,.0f}")
    return "\n".join(lines)


def draft(lesson: dict, rule_id: str) -> str:
    """The text of a pre-registration for a skip rule, for the owner to read, edit and commit as PRE-REGISTERED."""
    n, d = lesson.get("nomination") or {}, lesson.get("decision") or {}
    if lesson.get("stage") != "CONFIRMED" and not d:
        raise GovernanceError("only a CONFIRMED lesson can be drafted into a pre-registration")
    cell = f"{n['strategy']} entries with {n['dimension']} = {n['value']}"
    return f"""---
type: pre-registration
tags: [research, pre-registered, intraday, skip-rule, learning-loop]
date: {datetime.now().date().isoformat()}
status: DRAFT written by `fb learn draft`; becomes binding only when committed as `PRE-REGISTERED skip rule {rule_id}.md`
---

# PRE-REGISTERED skip rule {rule_id}

Authority: [[ADR-0004 one evidence standard]] ("a losing context becomes a hypothesis, never a fix"). This registers the *test*
of a skip rule; it changes no live rule. Lesson `{lesson['id']}`. Written before any shadow result exists.

## Hypothesis

{cell} are worse than the other {n['strategy']} trades taken the same day, and lose money net of costs.

## Evidence so far (found, then confirmed on sessions it had not seen)

* Found on every session up to {n.get('d0')}: {n.get('trades')} trades over {n.get('days')} sessions, {n.get('mean_bp', 0):+.1f} bp a trade,
  {n.get('contrast_bp', 0):+.1f} bp worse than the rest the same day, p {n.get('p', 0):.4f}, Benjamini-Hochberg q {n.get('q', 0):.3f} among
  {n.get('family_size')} cells of the {n.get('family')} family.
* Confirmed once on the {d.get('sessions_after')} sessions after that: {d.get('trades')} trades, {d.get('mean_bp', 0):+.1f} bp,
  {d.get('contrast_bp', 0):+.1f} bp worse than the rest, p {d.get('p', 0):.4f} against a threshold of {d.get('alpha', 0):.4f}.

## The rule (fixed here)

Decline a {n['strategy']} entry when `{n['dimension']}` = `{n['value']}` at the moment of the signal (the definition is
`learning/features.py`, identical in the loss review and in the engine). Nothing else about the strategy changes: sizing, stops,
targets, costs and exits stay as in live-v1.

## The forward test (shadow, then decision)

From `active_from` (set when the rule is added to `docs/shadow/skip-rules.v2.json`), each night the rule is asked of every closed live
trade whether it would have been declined. After at least {SH.MIN_SHADOW_DAYS} sessions and {SH.MIN_SKIPPED} declined trades the
verdict is:

* **PASSING** only if the declined trades lose net (mean below zero, t <= {SH.T_PASS} across days) *and* v1 minus those trades beats
  v1. PASSING is a request for the owner's decision, not a switch.
* **FAILING** if the declined trades would have made money (the rule removes winners): retire it.
* otherwise **INCONCLUSIVE**: keep collecting or retire.

On PASSING the owner may approve it (`fb learn approve {rule_id}`): a human, at a terminal, bound to this exact rule, expiring in
{DEFAULT_DAYS} days, after which it runs in the paper `live-v2` cohort beside the unchanged live-v1. It is watched for decay.

## What would show it is wrong

The declined group stops losing in later sessions (the nightly decay check marks it DECAYING); v2 does not beat v1 in the paper
cohort on the same days; the loss is explained by something that changed (market regime, a rule change in v1).

## Stated limits

One strategy and a small, recent sample. Skipping a trade can free exposure for a trade v1 could not take (the engine caps open
exposure); the shadow cannot credit it, the paper cohort can. The sample is live paper trading with an assumed fill model, not real
fills. A paper result is not a real-money result.

Information, not advice.
"""


def write_draft(lesson: dict, rule_id: str, *, directory: Path = DRAFT_DIR) -> Path:
    text = draft(lesson, rule_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"DRAFT skip rule {rule_id}.md"
    if path.exists():
        raise GovernanceError(f"{path} already exists; edit it instead")
    path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------------------------------------------------------ register, approve, retire

def register(lesson: dict, rule_id: str, prereg: str, active_from: str, *, path: Path = SH.RULES_FILE, repo: Path = Path("."),
             audit=None) -> SH.SkipRule:
    """Add a rule to the rules file as SHADOW (no effect on trading). The pre-registration must already be committed."""
    n = lesson.get("nomination") or {}
    if lesson.get("stage") not in ("CONFIRMED", "REGISTERED", "SHADOW"):
        raise GovernanceError("only a CONFIRMED lesson can be registered as a skip rule")
    doc = _read(path) if path.exists() else {"schema": SH.SCHEMA_ID, "rules": []}
    if any(r.get("id") == rule_id for r in doc["rules"]):
        raise GovernanceError(f"{rule_id} already exists")
    doc["rules"].append({"id": rule_id, "lesson": lesson["id"], "strategy": n["strategy"], "dimension": n["dimension"],
                         "value": n["value"], "prereg": prereg, "active_from": active_from, "status": "SHADOW"})
    tmp = path.with_suffix(".check.json")
    _write(tmp, doc)
    try:
        rules = SH.load_rules(tmp, repo=repo, audit=audit)               # the same validation the nightly job applies
    finally:
        tmp.unlink(missing_ok=True)
    _write(path, doc)
    return next(r for r in rules if r.id == rule_id)


def approve(rule_id: str, actor: str, store, *, path: Path = SH.RULES_FILE, days: int = DEFAULT_DAYS, now: datetime | None = None,
            interactive: bool | None = None, input_fn=input, out=print) -> dict:
    """The owner's decision to put a PASSING rule in force in the paper v2 cohort."""
    _need_person(interactive)
    if not actor.strip() or is_agent_actor(actor, "human"):
        raise GovernanceError(f"{actor!r} is not the name of a person")
    now = now or datetime.now(timezone.utc)
    doc = _read(path)
    r = _find(doc, rule_id)
    if r.get("status", "SHADOW") != "SHADOW":
        raise GovernanceError(f"{rule_id} is {r.get('status')}, not SHADOW")
    verdict = _latest_shadow(store, r)
    if not verdict or verdict.get("status") != "PASSING":
        raise GovernanceError(f"{rule_id} is not PASSING in shadow (latest: {verdict.get('status') if verdict else 'no result yet'}): "
                              "refused")
    lesson = store.lessons().get(r.get("lesson") or rule_id) or {"id": rule_id, "stage": "SHADOW"}
    out(card(lesson, verdict))
    out(f"\nApproving puts {rule_id} in force in the paper live-v2 cohort for {days} days. It changes no live-v1 behaviour and "
        "no real order can result.")
    if input_fn(f"Type APPROVE {rule_id} to confirm: ").strip() != f"APPROVE {rule_id}":
        raise GovernanceError("not confirmed; nothing changed")
    rule_view = SH.SkipRule(id=r["id"], strategy=r["strategy"], dimension=r["dimension"], value=r["value"], prereg=r["prereg"],
                            active_from=r["active_from"], lesson=r.get("lesson"))
    approval = {"kind": KIND, "subject": rule_id, "scope_sha256": scope_sha(rule_view), "actor_id": actor.strip(),
                "actor_type": "human", "approved_at_utc": now.isoformat(), "expires_at_utc": (now + timedelta(days=days)).isoformat(),
                "adr_ref": ADR}
    r["approval"], r["status"] = approval, "ACTIVE"
    _write(path, doc)
    lid = r.get("lesson") or rule_id
    store.append(lid, "APPROVED", actor=actor.strip(), session=rule_id, payload=approval)
    store.append(lid, "ACTIVATED", actor=actor.strip(), session=rule_id, payload={"rule": rule_id, "cohort": "live-v2"})
    return approval


def retire(rule_id: str, actor: str, reason: str, store, *, path: Path = SH.RULES_FILE, interactive: bool | None = None,
           input_fn=input) -> None:
    _need_person(interactive)
    if not actor.strip() or is_agent_actor(actor, "human") or not reason.strip():
        raise GovernanceError("a named person and a reason are required")
    doc = _read(path)
    r = _find(doc, rule_id)
    if input_fn(f"Type RETIRE {rule_id} to confirm: ").strip() != f"RETIRE {rule_id}":
        raise GovernanceError("not confirmed; nothing changed")
    r["status"] = "RETIRED"
    _write(path, doc)
    store.append(r.get("lesson") or rule_id, "RETIRED", actor=actor.strip(), session=rule_id,
                 payload={"rule": rule_id, "reason": reason.strip()})
