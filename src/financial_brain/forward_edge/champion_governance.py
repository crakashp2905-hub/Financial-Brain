"""Human-approved paper champion changes and automatic safe-policy rollback (FE-0129..0131)."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from financial_brain.forward_edge.approvals import (
    Approval,
    ApprovalError,
    validate_approval,
)
from financial_brain.forward_edge.custody import (
    CustodyStatus,
    Receipt,
    TrustRoot,
    classify_custody,
)


class ChampionGovernanceError(ValueError):
    """Champion transition is unauthorized, unattested or conflicts with durable state."""


AUTO_ROLLBACK_TRIGGERS = frozenset({"DRIFT", "OUTAGE", "ACCOUNTING_MISMATCH", "MANUAL_PAUSE"})
CHANGE_TYPES = frozenset({"PROMOTION", "DOWNGRADE", "HUMAN_EXCEPTION"})


@dataclass(frozen=True)
class ChampionPolicy:
    strategy_id: str
    model_sha256: str
    max_risk_fraction: float
    capital_fraction: float

    def __post_init__(self) -> None:
        if not self.strategy_id.strip():
            raise ChampionGovernanceError("strategy ID is required")
        for name, digest in (("model", self.model_sha256),):
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise ChampionGovernanceError(f"{name} hash must be lowercase SHA-256")
        for value in (self.max_risk_fraction, self.capital_fraction):
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ChampionGovernanceError("risk and capital fractions must be in [0, 1]")

    def body(self) -> dict[str, object]:
        return {"strategy_id": self.strategy_id, "model_sha256": self.model_sha256,
                "max_risk_fraction": self.max_risk_fraction,
                "capital_fraction": self.capital_fraction}

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.body(), sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class ChampionState:
    policy: ChampionPolicy
    execution_mode: str
    entries_allowed: bool
    live_enabled: bool
    transition_sequence: int
    event_sha256: str


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class ChampionGovernanceStore:
    """Hash-chained immutable champion transition store with paper-only promotion and fail-safe rollback."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("CREATE TABLE IF NOT EXISTS champion_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self._con.execute("CREATE TABLE IF NOT EXISTS champion_transitions (sequence INTEGER PRIMARY KEY, transition_id TEXT UNIQUE NOT NULL, event_sha256 TEXT NOT NULL, event_json TEXT NOT NULL)")
        self._con.execute("CREATE TRIGGER IF NOT EXISTS champion_transitions_no_update BEFORE UPDATE ON champion_transitions BEGIN SELECT RAISE(ABORT,'champion transitions are immutable'); END")
        self._con.execute("CREATE TRIGGER IF NOT EXISTS champion_transitions_no_delete BEFORE DELETE ON champion_transitions BEGIN SELECT RAISE(ABORT,'champion transitions are immutable'); END")
        self._con.execute("CREATE TRIGGER IF NOT EXISTS champion_metadata_no_update BEFORE UPDATE ON champion_metadata BEGIN SELECT RAISE(ABORT,'champion metadata is immutable'); END")
        self._con.execute("CREATE TRIGGER IF NOT EXISTS champion_metadata_no_delete BEFORE DELETE ON champion_metadata BEGIN SELECT RAISE(ABORT,'champion metadata is immutable'); END")
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def initialize(self, safe_policy: ChampionPolicy, *, now: datetime) -> ChampionState:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ChampionGovernanceError("initialization time must be timezone aware")
        metadata = self._con.execute("SELECT value FROM champion_metadata WHERE key='safe_policy'").fetchone()
        encoded_policy = _canonical(safe_policy.body())
        if metadata:
            if metadata[0] != encoded_policy:
                raise ChampionGovernanceError("safe policy is immutable after governance initialization")
            return self.state()
        if self._con.execute("SELECT 1 FROM champion_transitions LIMIT 1").fetchone():
            raise ChampionGovernanceError("transition log exists without safe policy metadata")
        self._con.execute("INSERT INTO champion_metadata VALUES ('safe_policy',?)", [encoded_policy])
        body = {"sequence": 0, "transition_id": "governance-initialization",
                "occurred_at": now.astimezone(timezone.utc).isoformat(),
                "transition_type": "INITIAL", "trigger": "BOOTSTRAP_SAFE_HOLD",
                "actor_id": "system", "actor_type": "system", "from_policy": None,
                "to_policy": safe_policy.body(), "execution_mode": "SAFE_HOLD",
                "entries_allowed": False, "live_enabled": False,
                "approval_sha256": None, "custody_status": None,
                "evidence_manifest_sha256": None, "active_order_count": 0,
                "unknown_order_count": 0, "previous_event_sha256": "0" * 64}
        digest = hashlib.sha256(_canonical(body).encode()).hexdigest()
        self._con.execute("INSERT INTO champion_transitions VALUES (?,?,?,?)",
                          [0, "governance-initialization", digest,
                           _canonical({**body, "event_sha256": digest})])
        self._con.commit()
        return self.state()

    def state(self) -> ChampionState:
        row = self._con.execute("SELECT sequence,event_sha256,event_json FROM champion_transitions ORDER BY sequence DESC LIMIT 1").fetchone()
        if row is None:
            raise ChampionGovernanceError("champion governance has not been initialized")
        event = json.loads(row[2])
        policy = ChampionPolicy(**event["to_policy"])
        return ChampionState(policy, event["execution_mode"], bool(event["entries_allowed"]),
                             bool(event["live_enabled"]), int(row[0]), str(row[1]))

    def _append(self, *, transition_id: str, transition_type: str, trigger: str,
                actor_id: str, actor_type: str, new_policy: ChampionPolicy,
                execution_mode: str, entries_allowed: bool, approval_sha256: str | None,
                custody_status: str | None, evidence_manifest_sha256: str | None,
                active_order_count: int, unknown_order_count: int, now: datetime) -> ChampionState:
        current = self.state()
        if not transition_id.strip() or not trigger.strip() or not actor_id.strip():
            raise ChampionGovernanceError("transition ID, trigger and actor are required")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ChampionGovernanceError("transition time must be timezone aware")
        if type(active_order_count) is not int or active_order_count < 0:
            raise ChampionGovernanceError("active order count must be non-negative")
        if type(unknown_order_count) is not int or not 0 <= unknown_order_count <= active_order_count:
            raise ChampionGovernanceError("unknown order count must be within active order count")
        prior_sequence = current.transition_sequence
        sequence = prior_sequence + 1
        body = {"sequence": sequence, "transition_id": transition_id,
                "occurred_at": now.astimezone(timezone.utc).isoformat(),
                "transition_type": transition_type, "trigger": trigger,
                "actor_id": actor_id, "actor_type": actor_type,
                "from_policy": current.policy.body(), "to_policy": new_policy.body(),
                "execution_mode": execution_mode, "entries_allowed": entries_allowed,
                "live_enabled": False, "approval_sha256": approval_sha256,
                "custody_status": custody_status,
                "evidence_manifest_sha256": evidence_manifest_sha256,
                "active_order_count": active_order_count,
                "unknown_order_count": unknown_order_count,
                "previous_event_sha256": current.event_sha256}
        digest = hashlib.sha256(_canonical(body).encode()).hexdigest()
        encoded = _canonical({**body, "event_sha256": digest})
        try:
            self._con.execute("INSERT INTO champion_transitions VALUES (?,?,?,?)",
                              [sequence, transition_id, digest, encoded])
            self._con.commit()
        except sqlite3.IntegrityError as exc:
            self._con.rollback()
            raise ChampionGovernanceError("transition sequence or ID already exists") from exc
        return self.state()

    def change_paper_champion(self, *, transition_id: str, policy: ChampionPolicy,
                              transition_type: str, reason: str,
                              strategy_approval: Approval,
                              evidence_manifest_sha256: str,
                              decision_at: datetime, earliest_entry_at: datetime,
                              receipt: Receipt | None,
                              trust_roots: Mapping[tuple[str, str], TrustRoot],
                              now: datetime, capital_approval: Approval | None = None) -> ChampionState:
        """Change the paper champion only with exact human approvals and verified pre-entry custody."""
        current = self.state()
        if transition_type not in CHANGE_TYPES:
            raise ChampionGovernanceError("unsupported human champion transition type")
        if not reason.strip():
            raise ChampionGovernanceError("human champion change needs a reason")
        custody = classify_custody(manifest_sha256=evidence_manifest_sha256,
                                   decision_at_utc=decision_at,
                                   entry_earliest_at_utc=earliest_entry_at,
                                   receipt=receipt, trust_roots=trust_roots)
        if custody is not CustodyStatus.VERIFIED:
            raise ChampionGovernanceError(f"independent pre-entry evidence is {custody.value}")
        try:
            validate_approval(strategy_approval, kind="STRATEGY_ENROLLMENT",
                              subject=policy.strategy_id, scope_sha256=policy.digest(), now=now)
            if ((current.policy.max_risk_fraction != policy.max_risk_fraction
                 or current.policy.capital_fraction != policy.capital_fraction)):
                if capital_approval is None:
                    raise ApprovalError("risk/capital change needs a separate human CAPITAL approval")
                validate_approval(capital_approval, kind="CAPITAL", subject=policy.strategy_id,
                                  scope_sha256=policy.digest(), now=now)
        except ApprovalError as exc:
            raise ChampionGovernanceError(f"human approval rejected: {exc}") from exc
        def approval_payload(approval: Approval | None) -> dict[str, object] | None:
            if approval is None:
                return None
            return {"kind": approval.kind, "subject": approval.subject,
                    "scope_sha256": approval.scope_sha256, "actor_id": approval.actor_id,
                    "actor_type": approval.actor_type,
                    "approved_at_utc": approval.approved_at_utc.astimezone(timezone.utc).isoformat(),
                    "expires_at_utc": approval.expires_at_utc.astimezone(timezone.utc).isoformat(),
                    "adr_ref": approval.adr_ref}
        approval_body = {"strategy": approval_payload(strategy_approval),
                         "capital": approval_payload(capital_approval)}
        approval_hash = hashlib.sha256(_canonical(approval_body).encode()).hexdigest()
        return self._append(transition_id=transition_id, transition_type=transition_type,
                            trigger=reason, actor_id=strategy_approval.actor_id,
                            actor_type="human", new_policy=policy,
                            execution_mode="PAPER", entries_allowed=True,
                            approval_sha256=approval_hash, custody_status=custody.value,
                            evidence_manifest_sha256=evidence_manifest_sha256,
                            active_order_count=0, unknown_order_count=0, now=now)

    def automatic_rollback(self, *, transition_id: str, trigger: str, now: datetime,
                           active_order_count: int, unknown_order_count: int) -> ChampionState:
        """Revert to frozen safe policy and halt entries on drift, outage or accounting mismatch."""
        if trigger not in AUTO_ROLLBACK_TRIGGERS:
            raise ChampionGovernanceError("unsupported automatic rollback trigger")
        row = self._con.execute("SELECT value FROM champion_metadata WHERE key='safe_policy'").fetchone()
        if row is None:
            raise ChampionGovernanceError("safe rollback policy is not initialized")
        safe_policy = ChampionPolicy(**json.loads(row[0]))
        return self._append(transition_id=transition_id, transition_type="AUTOMATIC_ROLLBACK",
                            trigger=trigger, actor_id="system:safety-monitor", actor_type="system",
                            new_policy=safe_policy, execution_mode="SAFE_HOLD", entries_allowed=False,
                            approval_sha256=None, custody_status=None,
                            evidence_manifest_sha256=None, active_order_count=active_order_count,
                            unknown_order_count=unknown_order_count, now=now)

    def events(self) -> tuple[dict[str, object], ...]:
        rows = self._con.execute("SELECT sequence,event_sha256,event_json FROM champion_transitions ORDER BY sequence").fetchall()
        previous = "0" * 64
        output = []
        for expected_sequence, (sequence, digest, encoded) in enumerate(rows):
            event = json.loads(encoded)
            claimed = event.pop("event_sha256")
            calculated = hashlib.sha256(_canonical(event).encode()).hexdigest()
            if (sequence != expected_sequence or claimed != digest or calculated != digest
                    or event["previous_event_sha256"] != previous):
                raise ChampionGovernanceError("champion transition hash chain is invalid")
            event["event_sha256"] = claimed
            previous = digest
            output.append(event)
        return tuple(output)
