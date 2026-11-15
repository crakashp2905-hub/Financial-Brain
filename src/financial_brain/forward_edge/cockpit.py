"""Static forward-session readiness cockpit from explicit operator telemetry (FE-0114)."""
from __future__ import annotations

import hashlib
import html
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class CockpitError(ValueError):
    """Invalid or internally inconsistent cockpit snapshot."""


HEALTH_STATES = frozenset({"HEALTHY", "STALE", "DOWN", "UNKNOWN", "NOT_APPLICABLE"})
GATE_STATES = frozenset({"PASS", "BLOCK", "UNKNOWN"})


@dataclass(frozen=True)
class CockpitGate:
    gate_id: str
    state: str
    reason: str

    def __post_init__(self) -> None:
        if not self.gate_id.strip() or not self.reason.strip():
            raise CockpitError("risk gate ID and reason are required")
        if self.state not in GATE_STATES:
            raise CockpitError("risk gate state must be PASS, BLOCK or UNKNOWN")


@dataclass(frozen=True)
class CockpitSnapshot:
    session_id: str
    as_of: datetime
    market_status: str
    feed_status: str
    feed_age_seconds: float | None
    max_feed_age_seconds: float
    broker_status: str
    risk_gates: tuple[CockpitGate, ...]
    active_paper_modes: tuple[str, ...]
    source_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise CockpitError("session ID is required")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise CockpitError("cockpit timestamp must be timezone aware")
        if any(state not in HEALTH_STATES for state in
               (self.market_status, self.feed_status, self.broker_status)):
            raise CockpitError("health states must be explicit known values")
        if self.feed_age_seconds is not None and self.feed_age_seconds < 0:
            raise CockpitError("feed age cannot be negative")
        if self.max_feed_age_seconds <= 0:
            raise CockpitError("maximum feed age must be positive")
        if any(not value.strip() for value in self.active_paper_modes + self.source_ids):
            raise CockpitError("paper mode and source IDs cannot be blank")

    @property
    def ready_for_paper(self) -> bool:
        return (self.market_status == "HEALTHY" and self.feed_status == "HEALTHY"
                and self.feed_age_seconds is not None
                and self.feed_age_seconds <= self.max_feed_age_seconds
                and self.broker_status in {"HEALTHY", "NOT_APPLICABLE"}
                and bool(self.active_paper_modes) and bool(self.risk_gates)
                and all(gate.state == "PASS" for gate in self.risk_gates))

    @property
    def not_ready_reasons(self) -> tuple[str, ...]:
        reasons: set[str] = set()
        if self.market_status != "HEALTHY":
            reasons.add(f"MARKET_{self.market_status}")
        if self.feed_status != "HEALTHY" or self.feed_age_seconds is None:
            reasons.add(f"FEED_{self.feed_status}")
        elif self.feed_age_seconds > self.max_feed_age_seconds:
            reasons.add("FEED_STALE")
        if self.broker_status not in {"HEALTHY", "NOT_APPLICABLE"}:
            reasons.add(f"BROKER_{self.broker_status}")
        if not self.active_paper_modes:
            reasons.add("NO_ACTIVE_PAPER_MODE")
        if not self.risk_gates:
            reasons.add("RISK_GATES_MISSING")
        reasons.update(f"RISK_{gate.state}:{gate.gate_id}:{gate.reason}"
                       for gate in self.risk_gates if gate.state != "PASS")
        return tuple(sorted(reasons))

    def digest(self) -> str:
        body = {
            "session_id": self.session_id,
            "as_of": self.as_of.astimezone(timezone.utc).isoformat(),
            "market_status": self.market_status, "feed_status": self.feed_status,
            "feed_age_seconds": self.feed_age_seconds,
            "max_feed_age_seconds": self.max_feed_age_seconds,
            "broker_status": self.broker_status,
            "risk_gates": [{"gate_id": item.gate_id, "state": item.state,
                            "reason": item.reason} for item in self.risk_gates],
            "active_paper_modes": list(self.active_paper_modes),
            "source_ids": list(self.source_ids),
        }
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()


def render_cockpit(snapshot: CockpitSnapshot, output: Path) -> Path:
    """Write a self-contained, escaped HTML status page. No data source is implied."""
    ready = snapshot.ready_for_paper
    readiness = "READY FOR PAPER" if ready else "NOT READY — NO TRADE"
    tone = "ready" if ready else "blocked"
    feed_age = "UNKNOWN" if snapshot.feed_age_seconds is None else f"{snapshot.feed_age_seconds:.3f} s"
    gates = "".join(
        "<tr><td>" + html.escape(gate.gate_id) + "</td><td>" + html.escape(gate.state)
        + "</td><td>" + html.escape(gate.reason) + "</td></tr>"
        for gate in snapshot.risk_gates)
    if not gates:
        gates = "<tr><td colspan='3'>No risk gate evidence recorded</td></tr>"
    reasons = "".join("<li>" + html.escape(reason) + "</li>"
                      for reason in snapshot.not_ready_reasons)
    if not reasons:
        reasons = "<li>All reported readiness checks pass</li>"
    modes = ", ".join(html.escape(mode) for mode in snapshot.active_paper_modes) or "None"
    sources = ", ".join(html.escape(source) for source in snapshot.source_ids) or "None recorded"
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Forward session cockpit</title><style>
body{{font:16px system-ui,sans-serif;margin:2rem auto;padding:0 1rem;max-width:900px;color:#18212b;background:#f4f6f8}}
main,section{{background:white;border:1px solid #d8dee6;border-radius:8px;padding:1rem;margin:1rem 0}}
h1{{font-size:1.4rem}}h2{{font-size:1.1rem}}table{{width:100%;border-collapse:collapse}}td,th{{padding:.5rem;text-align:left;border-bottom:1px solid #e1e5ea}}
.ready{{color:#12623b}}.blocked{{color:#a32622}}.tag{{font-weight:700}}small{{color:#566273}}
</style></head><body><main><h1>Financial Brain — Forward Session Cockpit</h1>
<p class="tag {tone}" role="status">{readiness}</p>
<p>Session: <b>{html.escape(snapshot.session_id)}</b> · As of: {html.escape(snapshot.as_of.isoformat())}</p>
<p>Evidence digest: <code>{snapshot.digest()}</code></p>
<section><h2>Market and connectivity</h2><p>Market: <b>{html.escape(snapshot.market_status)}</b> · Feed: <b>{html.escape(snapshot.feed_status)}</b>
({feed_age}; limit {snapshot.max_feed_age_seconds:.3f} s) · Broker: <b>{html.escape(snapshot.broker_status)}</b></p>
<p>Active paper modes: {modes}</p></section>
<section><h2>Risk gates</h2><table><thead><tr><th>Gate</th><th>State</th><th>Reason</th></tr></thead><tbody>{gates}</tbody></table></section>
<section><h2>Readiness reasons</h2><ul>{reasons}</ul><small>Readiness is a status summary from supplied snapshot fields; it does not create an order or attest to unprovided market data.</small></section>
<section><h2>Source identifiers</h2><p>{sources}</p></section></main></body></html>"""
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(page, encoding="utf-8")
    return destination
