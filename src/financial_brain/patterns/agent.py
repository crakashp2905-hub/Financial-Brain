"""Read-only agent boundary for chart-pattern explanations (stage 19: CP-0298..0310).

The agent can list, explain and trace stored records. It has no tool that places,
modifies or simulates an order, changes credentials, or edits the registry. Every
number in an answer is copied from a stored record; the agent never computes a
price, a probability or a risk verdict.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any

ALLOWED_TOOLS = frozenset({"list_patterns", "explain_event", "retrieve_trace"})
MAX_ROWS = 100
MAX_CALLS_PER_QUERY = 5
TICKER = re.compile(r"^[A-Z0-9&\-]{1,20}$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ALLOWED_STATES = frozenset({"CANDIDATE", "CONFIRMED", "FAILED"})
REFUSALS = (
    (re.compile(r"guarantee\w*\s+(profit|return)", re.I), "GUARANTEED_PROFIT"),
    (re.compile(r"zero[\s-]+loss", re.I), "ZERO_LOSS"),
    (re.compile(r"\b(place|send|submit)\b.*\border\b", re.I), "LIVE_ORDER"),
)


class AgentRefusal(ValueError):
    """A request the agent will not carry out, with a machine-readable code."""


class ToolNotAllowed(PermissionError):
    pass


def validate_query(*, ticker: str | None = None, state: str | None = None, start: str | None = None,
                   end: str | None = None, limit: int = 20) -> dict[str, Any]:
    """Strict enums, ranges and limits; anything malformed is rejected before any data access (CP-0299)."""
    if ticker is not None and not TICKER.match(ticker):
        raise AgentRefusal("INVALID_TICKER")
    if state is not None and state not in ALLOWED_STATES:
        raise AgentRefusal("INVALID_STATE")
    for label, value in (("start", start), ("end", end)):
        if value is not None and not ISO_DATE.match(value):
            raise AgentRefusal(f"INVALID_{label.upper()}")
    if start and end and start > end:
        raise AgentRefusal("INVALID_RANGE")
    if not 1 <= limit <= MAX_ROWS:
        raise AgentRefusal("LIMIT_OUT_OF_RANGE")
    return {"ticker": ticker, "state": state, "start": start, "end": end, "limit": limit}


def check_request(text: str) -> None:
    """Refuse guarantees, zero-loss claims and live-order requests (CP-0305)."""
    for pattern, code in REFUSALS:
        if pattern.search(text):
            raise AgentRefusal(code)


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HTML = re.compile(r"<[^>]+>")


def sanitize_untrusted(text: str) -> str:
    """Book text, ticker fields and CSV headers are data: links and markup become inert text (CP-0300, CP-0306)."""
    text = _MD_LINK.sub(r"\1", text)
    text = _HTML.sub("", text)
    return _CONTROL.sub("", text)[:500]


_SECRET = re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[=:]\s*\S+")
_ACCOUNT = re.compile(r"\b\d{8,}\b")
_WINDOWS_PATH = re.compile(r"[A-Za-z]:\\[^\s]+")


def redact(text: str) -> str:
    """Strip tokens, long account-like numbers and local file paths before export (CP-0307)."""
    text = _SECRET.sub(r"\1=[REDACTED]", text)
    text = _ACCOUNT.sub("[ACCOUNT]", text)
    return _WINDOWS_PATH.sub("[PATH]", text)


@dataclass
class AuditLog:
    """Bounded, redacted record of agent calls (CP-0308)."""

    capacity: int = 200
    _entries: deque = field(default_factory=deque)

    def record(self, tool: str, params: dict[str, Any]) -> None:
        safe = {k: redact(str(v)) for k, v in params.items() if k not in {"token", "secret"}}
        self._entries.append({"tool": tool, "params": safe})
        while len(self._entries) > self.capacity:
            self._entries.popleft()

    def __len__(self) -> int:
        return len(self._entries)


class ReadOnlyAgent:
    """Dispatches only allow-listed, read-only tools over stored records."""

    def __init__(self, records: dict[str, dict[str, Any]], audit: AuditLog | None = None) -> None:
        self._records = records
        self.audit = audit if audit is not None else AuditLog()  # an empty log is falsy, so test None
        self._calls = 0

    def call(self, tool: str, **params: Any) -> dict[str, Any]:
        if tool not in ALLOWED_TOOLS:
            raise ToolNotAllowed(tool)
        self._calls += 1
        if self._calls > MAX_CALLS_PER_QUERY:
            raise AgentRefusal("TOO_MANY_CALLS")
        self.audit.record(tool, params)
        if tool == "list_patterns":
            q = validate_query(**{k: v for k, v in params.items() if k in {"ticker", "state", "start", "end", "limit"}})
            rows = [r for r in self._records.values()
                    if (q["ticker"] is None or r["ticker"] == q["ticker"])
                    and (q["state"] is None or r["state"] == q["state"])]
            return {"rows": rows[: q["limit"]], "truncated": len(rows) > q["limit"]}
        if tool == "explain_event":
            record = self._records.get(str(params.get("event_id", "")))
            if record is None:
                return {"error": "UNKNOWN_EVENT"}
            return explain(record)
        record = self._records.get(str(params.get("event_id", "")))
        return {"trace": record.get("trace", {}) if record else {}, "error": None if record else "UNKNOWN_EVENT"}


REJECTION_TEXT: dict[str, str] = {
    "INELIGIBLE_SESSION": "the session is a circuit, suspension or auction-only session",
    "KILL_SWITCH_ACTIVE": "the kill switch blocks new entries",
    "SYMBOL_LIMIT": "the symbol weight would exceed its limit",
    "SECTOR_LIMIT": "the sector weight would exceed its limit",
    "DRAWDOWN_LIMIT": "drawdown is beyond the limit",
    "LEVERAGE_LIMIT": "leverage would exceed the limit",
    "STALE_BARS": "the bars are stale",
    "AMBIGUOUS_GEOMETRY": "the geometry cannot be resolved from completed bars",
    "LOW_LIQUIDITY": "liquidity is below the threshold",
    "INSUFFICIENT_EVIDENCE": "no evidence beyond DESCRIPTIVE exists",
    "MISSING_CALIBRATION": "no calibration exists; no probability is shown",
}


def explain_rejection(codes: list[str]) -> list[dict[str, str]]:
    """Each rejection code with plain text; unknown codes are reported, not hidden (CP-0302)."""
    return [
        {"code": c, "reason": REJECTION_TEXT.get(c, "unrecognised rejection code"), "authorises_trade": "false"}
        for c in codes
    ]


def explain(record: dict[str, Any]) -> dict[str, Any]:
    """Deterministic, rule-based explanation from stored fields only (CP-0309, CP-0304).

    Numbers are copied from the record; nothing is inferred, and the evidence tier is shown.
    """
    tier = record.get("evidence_tier", "DESCRIPTIVE")
    text = (
        f"{record['detector']} on {record['ticker']} is {record['state']} "
        f"(candidate known at bar {record['candidate_known_index']}). "
        f"Evidence tier: {tier}. Descriptive shape only; not a recommendation and not calibrated."
    )
    return {"explanation": sanitize_untrusted(text), "evidence_tier": tier, "source_event": record.get("event_id")}
