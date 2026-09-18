"""Evidence ledger (C09) - every claim traceable to the bytes it came from.

ARCHITECTURE.md §5.4: citation is not enough. Each claim the system makes - "Nifty 50
closed at 25,114", "the market is RISK_OFF", "BEL announced an order win" - is minted as
an immutable evidence row that answers:

    what does it claim?            kind, subject, claim, value
    where from?                    source, source_tier, lake_key -> sha256 + url
    true as of when?               as_of (event time), published_at (became public)
    when did we know?              observed_at
    how was it derived?            derivation (rule / version)
    how sure?                      confidence, quality
    who relied on it?              evidence_use

Two properties are enforced here rather than hoped for:

* **Immutable.** No UPDATE, ever. A correction is a *new* row whose ``supersedes``
  points at the old one; the old row stays, so any past decision can be replayed
  against exactly what was believed at the time.
* **Idempotent.** ``evidence_id`` is a hash of what the claim says (kind, subject,
  as_of, value, derivation), so re-minting the same fact is a no-op, not a duplicate.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone


def _canon(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, float):
        return round(v, 10)
    if isinstance(v, dict):
        return {k: _canon(x) for k, x in sorted(v.items())}
    if isinstance(v, (list, tuple)):
        return [_canon(x) for x in v]
    return v


def evidence_id(kind: str, subject: str, as_of, value, derivation: str) -> str:
    body = json.dumps(_canon({"kind": kind, "subject": subject, "as_of": as_of,
                              "value": value, "derivation": derivation}),
                      sort_keys=True, separators=(",", ":"))
    return "ev_" + hashlib.sha256(body.encode()).hexdigest()[:24]


def mint(con, *, kind: str, subject: str, as_of, claim: str, value, source: str,
         source_tier: int, derivation: str, lake_key: str | None = None,
         published_at=None, confidence: str = "high", quality: str = "ok",
         supersedes: str | None = None, inputs: list[str] | None = None) -> str:
    """Record a claim; returns its evidence_id. Re-minting identical content is a no-op."""
    eid = evidence_id(kind, subject, as_of, value, derivation)
    con.execute(
        """INSERT INTO evidence (evidence_id, kind, subject, as_of, published_at, claim,
           value, source, source_tier, lake_key, derivation, confidence, quality,
           supersedes, inputs, observed_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (evidence_id) DO NOTHING""",
        [eid, kind, subject, as_of, published_at, claim, json.dumps(_canon(value)), source,
         source_tier, lake_key, derivation, confidence, quality, supersedes,
         json.dumps(inputs) if inputs else None, datetime.now(timezone.utc)])
    return eid


def correct(con, old_id: str, **fields) -> str:
    """Record a correction as a new row superseding ``old_id``; the old row is kept."""
    return mint(con, supersedes=old_id, **fields)


def use(con, evidence_ids, *, used_by_kind: str, used_by_id: str) -> None:
    """Record that a world state / decision / brief relied on these claims."""
    now = datetime.now(timezone.utc)
    con.executemany(
        """INSERT INTO evidence_use (evidence_id, used_by_kind, used_by_id, used_at)
           VALUES (?,?,?,?) ON CONFLICT DO NOTHING""",
        [[e, used_by_kind, used_by_id, now] for e in dict.fromkeys(evidence_ids)])


def current(con, eid: str) -> str:
    """Follow supersessions forward to the latest version of a claim."""
    seen = {eid}
    while True:
        nxt = con.execute("SELECT evidence_id FROM evidence WHERE supersedes = ? "
                          "ORDER BY observed_at DESC LIMIT 1", [eid]).fetchone()
        if not nxt or nxt[0] in seen:
            return eid
        eid = nxt[0]
        seen.add(eid)


def trace(con, eid: str) -> dict:
    """The full provenance chain of one claim, down to the stored bytes."""
    row = con.execute(
        """SELECT e.evidence_id, e.kind, e.subject, e.as_of, e.published_at, e.claim,
                  e.value, e.source, e.source_tier, e.derivation, e.confidence, e.quality,
                  e.supersedes, e.observed_at, e.lake_key, m.sha256, m.url, m.retrieved_at,
                  e.inputs
           FROM evidence e LEFT JOIN lake_manifest m ON m.key = e.lake_key
           WHERE e.evidence_id = ?""", [eid]).fetchone()
    if not row:
        raise KeyError(eid)
    keys = ["evidence_id", "kind", "subject", "as_of", "published_at", "claim", "value",
            "source", "source_tier", "derivation", "confidence", "quality", "supersedes",
            "observed_at", "lake_key", "sha256", "url", "retrieved_at", "inputs"]
    out = dict(zip(keys, row))
    out["value"] = json.loads(out["value"]) if out["value"] else None
    out["inputs"] = json.loads(out["inputs"]) if out["inputs"] else []
    out["used_by"] = [dict(zip(["used_by_kind", "used_by_id", "used_at"], r)) for r in
                      con.execute("SELECT used_by_kind, used_by_id, used_at FROM evidence_use "
                                  "WHERE evidence_id = ? ORDER BY used_at", [eid]).fetchall()]
    out["superseded_by"] = (lambda c: None if c == eid else c)(current(con, eid))
    return out
