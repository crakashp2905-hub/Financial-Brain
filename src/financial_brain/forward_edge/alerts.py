"""Deduplicated operational alerts and entry-halt state (FE-0074)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
from zoneinfo import ZoneInfo

ALERT_TYPES = frozenset({
    "STALE_QUOTE", "STALE_DATA", "TOKEN_EXPIRED", "SESSION_ABORTED", "DUPLICATE_ORDER",
    "LEDGER_DIFFERENCE", "ORDER_EXECUTION", "MF_PENDING", "ETF_DISLOCATION",
    "OPTIONS_EXPIRY", "RISK_BUDGET_CHANGE",
})
SEVERITY_RANK = {"INFO": 0, "WARNING": 1, "HIGH": 2, "CRITICAL": 3}


class AlertError(ValueError):
    """Invalid operational alert or transition."""


@dataclass(frozen=True)
class Alert:
    alert_id: str
    root_cause_id: str
    session_id: str
    alert_type: str
    severity: str
    occurred_at: datetime
    details: str

    def __post_init__(self) -> None:
        if not all(x.strip() for x in (self.alert_id, self.root_cause_id, self.session_id, self.details)):
            raise AlertError("alert identity, session and details are required")
        if self.alert_type not in ALERT_TYPES or self.severity not in SEVERITY_RANK:
            raise AlertError("unknown alert type or severity")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise AlertError("occurred_at must be timezone aware")

    @classmethod
    def create(cls, *, root_cause_id: str, session_id: str, alert_type: str, severity: str,
               occurred_at: datetime, details: str) -> "Alert":
        raw = f"{root_cause_id}|{session_id}|{alert_type}".encode()
        return cls(hashlib.sha256(raw).hexdigest(), root_cause_id, session_id, alert_type,
                   severity, occurred_at, details)

    def digest(self) -> str:
        body = {"alert_id": self.alert_id, "root_cause_id": self.root_cause_id,
                "session_id": self.session_id, "alert_type": self.alert_type,
                "severity": self.severity, "occurred_at": self.occurred_at.astimezone(timezone.utc).isoformat(),
                "details": self.details}
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class AlertBook:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("""CREATE TABLE IF NOT EXISTS alerts (
            alert_id TEXT PRIMARY KEY, root_cause_id TEXT NOT NULL, session_id TEXT NOT NULL,
            alert_type TEXT NOT NULL, severity TEXT NOT NULL, occurred_at TEXT NOT NULL,
            details TEXT NOT NULL, digest TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0,
            cleared INTEGER NOT NULL DEFAULT 0
        )""")
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def raise_alert(self, alert: Alert) -> bool:
        prior = self._con.execute("SELECT digest FROM alerts WHERE alert_id=?", [alert.alert_id]).fetchone()
        if prior:
            if prior[0] != alert.digest():
                raise AlertError("dedupe alert ID reused with changed content")
            return False
        self._con.execute("INSERT INTO alerts(alert_id,root_cause_id,session_id,alert_type,severity,occurred_at,details,digest) VALUES (?,?,?,?,?,?,?,?)",
                          [alert.alert_id, alert.root_cause_id, alert.session_id, alert.alert_type,
                           alert.severity, alert.occurred_at.astimezone(timezone.utc).isoformat(),
                           alert.details, alert.digest()])
        self._con.commit()
        return True

    def acknowledge(self, alert_id: str) -> None:
        cur = self._con.execute("UPDATE alerts SET acknowledged=1 WHERE alert_id=?", [alert_id])
        if cur.rowcount != 1:
            raise AlertError("unknown alert")
        self._con.commit()

    def clear(self, alert_id: str) -> None:
        cur = self._con.execute("UPDATE alerts SET cleared=1 WHERE alert_id=?", [alert_id])
        if cur.rowcount != 1:
            raise AlertError("unknown alert")
        self._con.commit()

    def halt_entries(self, session_id: str) -> bool:
        row = self._con.execute("SELECT COUNT(*) FROM alerts WHERE session_id=? AND cleared=0 AND severity IN ('HIGH','CRITICAL')",
                                [session_id]).fetchone()
        return bool(row[0])

    def active(self, session_id: str) -> list[dict[str, object]]:
        rows = self._con.execute("SELECT alert_id,root_cause_id,alert_type,severity,occurred_at,details,acknowledged FROM alerts WHERE session_id=? AND cleared=0 ORDER BY occurred_at,alert_id",
                                 [session_id]).fetchall()
        names = ("alert_id", "root_cause_id", "alert_type", "severity", "occurred_at", "details", "acknowledged")
        return [dict(zip(names, row, strict=True)) for row in rows]


def evaluate_desk_alerts(
    *,
    session_id: str,
    occurred_at: datetime,
    order_states: tuple[dict, ...] = (),
    stale_feeds: tuple[dict, ...] = (),
    pending_mutual_funds: tuple[dict, ...] = (),
    etf_premiums: tuple[dict, ...] = (),
    option_expiries: tuple[dict, ...] = (),
    risk_budget_changes: tuple[dict, ...] = (),
    stale_after_seconds: float = 60.0,
    mf_pending_after_days: int = 1,
    etf_dislocation_threshold_bps: float = 100.0,
    option_expiry_window_days: int = 3,
) -> list[Alert]:
    """Create deterministic alerts from caller-supplied desk evidence; never fetch or infer data.

    Each input tuple contains normalized evidence rows. This keeps thresholds explicit and
    leaves data acquisition/identity reconciliation to the owning desk adapters.
    """
    if not session_id.strip() or occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise AlertError("desk alerts need a session ID and timezone-aware event time")
    thresholds = (stale_after_seconds, etf_dislocation_threshold_bps)
    if any(not math.isfinite(value) or value < 0 for value in thresholds):
        raise AlertError("desk alert numeric thresholds must be finite and non-negative")
    if type(mf_pending_after_days) is not int or mf_pending_after_days < 0:
        raise AlertError("MF pending threshold must be a non-negative whole number of days")
    if type(option_expiry_window_days) is not int or option_expiry_window_days < 0:
        raise AlertError("option expiry window must be a non-negative whole number of days")
    out: list[Alert] = []

    def emit(cause: str, kind: str, severity: str, text: str) -> None:
        out.append(Alert.create(root_cause_id=cause, session_id=session_id, alert_type=kind,
                                severity=severity, occurred_at=occurred_at, details=text))

    for row in order_states:
        order_id, state = str(row.get("order_id", "")).strip(), str(row.get("state", "")).upper()
        if not order_id or state not in {"PARTIAL", "FILLED", "REJECTED", "UNKNOWN", "CANCELED", "MISSED"}:
            raise AlertError("order alert needs an ID and a recognized terminal or partial state")
        severity = "HIGH" if state == "UNKNOWN" else "WARNING"
        emit(f"order:{order_id}:{state}", "ORDER_EXECUTION", severity,
             f"Paper order {order_id} state is {state}; reconcile partial or ambiguous outcomes.")

    for row in stale_feeds:
        feed = str(row.get("feed_id", "")).strip()
        age = row.get("age_seconds")
        if not feed or isinstance(age, bool) or not isinstance(age, (int, float)) or not math.isfinite(age) or age < 0:
            raise AlertError("stale-feed alert needs an ID and finite non-negative age")
        if age > stale_after_seconds:
            severity = "HIGH" if bool(row.get("entry_critical")) else "WARNING"
            emit(f"feed:{feed}:stale", "STALE_DATA", severity,
                 f"Feed {feed} age {age:.1f}s exceeds {stale_after_seconds:.1f}s threshold.")

    for row in pending_mutual_funds:
        item_id = str(row.get("item_id", "")).strip()
        stage = str(row.get("stage", "")).strip().upper()
        age_days = row.get("age_days")
        if (not item_id or stage not in {"UNITS", "PAYOUT"} or type(age_days) is not int
                or age_days < 0):
            raise AlertError("MF pending alert needs item ID, UNITS/PAYOUT stage and whole age days")
        if age_days >= mf_pending_after_days:
            emit(f"mf:{item_id}:{stage}:pending", "MF_PENDING", "WARNING",
                 f"MF {stage.lower()} {item_id} remains pending for {age_days} days.")

    for row in etf_premiums:
        instrument = str(row.get("instrument_id", "")).strip()
        premium = row.get("premium_discount_bps")
        if (not instrument or isinstance(premium, bool) or not isinstance(premium, (int, float))
                or not math.isfinite(premium)):
            raise AlertError("ETF alert needs instrument ID and finite premium/discount bps")
        if abs(premium) > etf_dislocation_threshold_bps:
            emit(f"etf:{instrument}:dislocation", "ETF_DISLOCATION", "WARNING",
                 f"ETF {instrument} midquote premium/discount is {premium:+.1f} bps versus fresh iNAV.")

    for row in option_expiries:
        contract = str(row.get("contract_id", "")).strip()
        days_left = row.get("days_left")
        if not contract or type(days_left) is not int:
            raise AlertError("option expiry alert needs a contract ID and whole days left")
        if 0 <= days_left <= option_expiry_window_days:
            emit(f"option:{contract}:expiry", "OPTIONS_EXPIRY", "WARNING",
                 f"Open option contract {contract} expires in {days_left} calendar days.")

    for row in risk_budget_changes:
        budget_id = str(row.get("budget_id", "")).strip()
        old, new = row.get("old_value"), row.get("new_value")
        if (not budget_id or isinstance(old, bool) or isinstance(new, bool)
                or not isinstance(old, (int, float)) or not isinstance(new, (int, float))
                or not math.isfinite(old) or not math.isfinite(new)):
            raise AlertError("risk-budget alert needs an ID and finite old/new values")
        if old != new:
            emit(f"risk-budget:{budget_id}:{old}:{new}", "RISK_BUDGET_CHANGE", "WARNING",
                 f"Risk budget {budget_id} changed from {old:g} to {new:g}.")
    return out


def mutual_fund_pending_rows(connection, *, as_of: datetime) -> tuple[dict, ...]:
    """Read unresolved MF units and payout states from the migrated paper-book database."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise AlertError("MF pending query as_of must be timezone aware")
    cutoff = as_of.astimezone(timezone.utc)
    local_day = as_of.astimezone(ZoneInfo("Asia/Kolkata")).date()
    rows: list[dict] = []
    purchases = connection.execute("""SELECT o.account_id, e.order_id, e.transaction_at
        FROM mf_paper_purchase_eligibility e JOIN mf_paper_orders o USING (order_id)
        WHERE e.state='NAV_PENDING' AND o.transaction_type='PURCHASE'
          AND e.transaction_at<=? AND e.updated_at<=? AND o.created_at<=?
        ORDER BY o.account_id, e.order_id""", [cutoff, cutoff, cutoff]).fetchall()
    for account_id, order_id, transaction_at in purchases:
        transaction_day = transaction_at.astimezone(ZoneInfo("Asia/Kolkata")).date()
        rows.append({"item_id": f"{account_id}:{order_id}", "stage": "UNITS",
                     "age_days": max(0, (local_day - transaction_day).days)})
    redemptions = connection.execute("""SELECT account_id, settlement_id, state, requested_on
        FROM mf_paper_redemption_settlements
        WHERE state IN ('REDEMPTION_PENDING','VALUE_CONFIRMED','PAYOUT_PENDING')
          AND created_at<=? AND updated_at<=?
        ORDER BY account_id, settlement_id""", [cutoff, cutoff]).fetchall()
    for account_id, settlement_id, state, requested_on in redemptions:
        rows.append({"item_id": f"{account_id}:{settlement_id}",
                     "stage": "UNITS" if state == "REDEMPTION_PENDING" else "PAYOUT",
                     "age_days": max(0, (local_day - requested_on).days)})
    return tuple(rows)
