"""Monitoring a decision's invalidation conditions (C17, Phase 2 exit test).

A decision must state what would prove it wrong. Stating it is not enough: an
invalidation condition nobody re-checks is decoration. This module re-evaluates the
*machine-checkable* conditions of every live decision against current data and records a
trigger as evidence - so "the thesis broke" is a dated, sourced claim, not a memory.

Conditions live on a decision in two forms:

``invalidation_conditions``   prose, for a human ("management cuts guidance")
``invalidation_checks``       typed, for this module, e.g.
                              ``{"check": "price_below", "level": 1180}``

Prose with no typed twin is reported as **unmonitored** rather than silently treated as
satisfied: that gap is itself the finding. Every check is deterministic over Tier-1
data - no model is ever asked whether a thesis still holds.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from ..evidence import ledger
from . import record

LIVE = ("DRAFT", "EVIDENCE_VERIFIED", "RISK_REVIEWED", "PAPER_CANDIDATE",
        "HUMAN_APPROVED", "PROPOSED_TO_BROKER", "EXECUTED")


def _close(con, isin: str, d: date):
    row = con.execute("""SELECT close_price, business_date FROM eod_prices
                         WHERE isin = ? AND business_date <= ? AND close_price IS NOT NULL
                         ORDER BY business_date DESC LIMIT 1""", [isin, d]).fetchone()
    return (float(row[0]), row[1]) if row else (None, None)


def check_price_below(con, c, isin, d, spec):
    px, on = _close(con, isin, d)
    if px is None:
        return None, "no close available"
    return px < spec["level"], f"close {px:.2f} on {on} vs level {spec['level']:.2f}"


def check_price_above(con, c, isin, d, spec):
    px, on = _close(con, isin, d)
    if px is None:
        return None, "no close available"
    return px > spec["level"], f"close {px:.2f} on {on} vs level {spec['level']:.2f}"


def check_drawdown_from(con, c, isin, d, spec):
    """Fired when the close has fallen ``pct`` from ``reference`` (typically the entry)."""
    px, on = _close(con, isin, d)
    ref = spec.get("reference")
    if px is None or not ref:
        return None, "no close, or no reference price on the spec"
    fall = (ref - px) / ref
    return fall >= spec["pct"], f"close {px:.2f} on {on} is {-fall:+.1%} from {ref:.2f}"


def check_event_of_type(con, c, isin, d, spec):
    """Fired by any announcement of these types since the decision was taken."""
    since = spec.get("since") or c.get("_created_on")
    types = list(spec["types"])
    marks = ",".join("?" * len(types))
    rows = con.execute(f"""SELECT event_type, business_date, LEFT(headline, 80)
        FROM announcements WHERE isin = ? AND business_date > ? AND business_date <= ?
        AND event_type IN ({marks}) ORDER BY business_date DESC LIMIT 3""",
                       [isin, since, d, *types]).fetchall()
    if not rows:
        return False, f"no {'/'.join(types)} filing since {since}"
    return True, "; ".join(f"{r[1]} {r[0]}: {r[2]}" for r in rows)


def check_adverse_tone(con, c, isin, d, spec):
    """Fired by an *accepted* adverse reading on a filing since the decision.

    Accepted only: a reading that did not clear its model's calibrated bar is not
    evidence that a thesis broke.
    """
    since = spec.get("since") or c.get("_created_on")
    rows = con.execute("""SELECT a.business_date, t.model, LEFT(a.headline, 70)
        FROM announcement_tone t JOIN announcements a USING (news_id)
        WHERE a.isin = ? AND t.accepted AND t.tone = 'negative'
        AND a.business_date > ? AND a.business_date <= ?
        ORDER BY a.business_date DESC LIMIT 3""", [isin, since, d]).fetchall()
    if not rows:
        return False, f"no accepted adverse reading since {since}"
    return True, "; ".join(f"{r[0]} ({r[1]}): {r[2]}" for r in rows)


def check_pledge_increase(con, c, isin, d, spec):
    since = spec.get("since") or c.get("_created_on")
    n = con.execute("""SELECT COUNT(*) FROM holder_filings WHERE isin = ?
                       AND relation = 'PLEDGE' AND business_date > ?
                       AND business_date <= ?""", [isin, since, d]).fetchone()[0]
    return n > spec.get("max_filings", 0), f"{n} pledge filings since {since}"


CHECKS = {"price_below": check_price_below, "price_above": check_price_above,
          "drawdown_from": check_drawdown_from, "event_of_type": check_event_of_type,
          "adverse_tone": check_adverse_tone, "pledge_increase": check_pledge_increase}


def evaluate(con, did: str, as_of: date) -> dict:
    """Evaluate one decision's typed checks. Read-only - no evidence is minted here."""
    c = record.content(con, did)
    created = con.execute("SELECT created_at FROM decisions WHERE decision_id = ?",
                          [did]).fetchone()[0]
    c["_created_on"] = created.date() if hasattr(created, "date") else created
    checks = c.get("invalidation_checks") or []
    results, triggered = [], []
    for spec in checks:
        fn = CHECKS.get(spec.get("check"))
        if not fn:
            results.append({"spec": spec, "state": "unknown_check",
                            "detail": f"no such check: {spec.get('check')!r}"})
            continue
        try:
            fired, detail = fn(con, c, c["isin"], as_of, spec)
        except Exception as e:            # noqa: BLE001 - a malformed spec is a finding
            results.append({"spec": spec, "state": "error",
                            "detail": f"{type(e).__name__}: {e}"})
            continue
        state = "unknown" if fired is None else ("triggered" if fired else "holding")
        results.append({"spec": spec, "state": state, "detail": detail})
        if fired:
            triggered.append(results[-1])
    prose = c.get("invalidation_conditions") or []
    unmonitored = prose[len(checks):] if len(prose) > len(checks) else []
    return {"decision_id": did, "isin": c["isin"], "as_of": as_of, "results": results,
            "triggered": triggered, "unmonitored": unmonitored}


def live_decisions(con) -> list[str]:
    marks = ",".join("?" * len(LIVE))
    return [r[0] for r in con.execute(f"""
        SELECT e.decision_id FROM (
            SELECT decision_id, to_state, ROW_NUMBER() OVER (PARTITION BY decision_id
                   ORDER BY seq DESC) rk FROM decision_events) e
        WHERE e.rk = 1 AND e.to_state IN ({marks})""", list(LIVE)).fetchall()]


def run(con, as_of: date | None = None, *, actor: str = "agent:monitor") -> dict:
    """Evaluate every live decision; record each trigger once, as evidence and an event."""
    d = as_of or date.today()
    out = {"checked": 0, "triggered": 0, "unmonitored_conditions": 0, "alerts": []}
    for did in live_decisions(con):
        ev = evaluate(con, did, d)
        out["checked"] += 1
        out["unmonitored_conditions"] += len(ev["unmonitored"])
        for hit in ev["triggered"]:
            eid = ledger.mint(
                con, kind="invalidation", subject=ev["isin"],
                as_of=datetime.combine(d, datetime.min.time()),
                claim=f"invalidation condition met for {did}: "
                      f"{hit['spec']['check']} - {hit['detail']}",
                value={"decision_id": did, "spec": hit["spec"], "detail": hit["detail"]},
                source="DERIVED", source_tier=1,
                derivation=f"decisions/monitor {hit['spec']['check']}")
            if con.execute("""SELECT 1 FROM decision_alerts WHERE decision_id = ?
                              AND evidence_id = ?""", [did, eid]).fetchone():
                continue          # same condition, same day, same facts: already raised
            con.execute("""INSERT INTO decision_alerts (decision_id, as_of, check_name,
                           detail, evidence_id, raised_at) VALUES (?,?,?,?,?,?)""",
                        [did, d, hit["spec"]["check"], hit["detail"], eid,
                         datetime.now(timezone.utc)])
            state = record._state(con, did)
            record._event(con, did, state, state, actor,
                          f"invalidation: {hit['spec']['check']} - {hit['detail']}")
            out["triggered"] += 1
            out["alerts"].append({"decision_id": did, "isin": ev["isin"],
                                  "check": hit["spec"]["check"], "detail": hit["detail"]})
    return out
