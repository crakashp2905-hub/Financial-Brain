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


# ---- merged from main (2026-11-18): definitions main's modules depend on; the branch's own
# ---- definitions above are unchanged.
import re
from decimal import Decimal
from html import escape
from financial_brain.forward_edge.registry import Status


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{escape(title)}</title>"
            "<style>.failed{color:#b00020}.promotable{color:#0a7d2c}.notrade{color:#555}</style></head>"
            f"<body><h1>{escape(title)}</h1>{body}</body></html>")


_ACTION_PATTERNS = (re.compile(r"<form", re.I), re.compile(r"<button", re.I), re.compile(r"method\s*=", re.I),
                    re.compile(r"<input", re.I), re.compile(r"onclick", re.I), re.compile(r"fetch\(", re.I))


def assert_read_only(html: str) -> None:
    """FE-0122: a page that could submit anything is refused."""
    for pat in _ACTION_PATTERNS:
        if pat.search(html):
            raise CockpitError(f"page contains an action element matching {pat.pattern}")


def landing(*, feed_ok: bool, broker: str, market_status: str, risk_gates_clear: bool, mode: str, tier: str,
            reasons: list[str]) -> str:
    trustworthy = feed_ok and risk_gates_clear and broker == "DISABLED" and not reasons
    verdict = "TRUSTWORTHY" if trustworthy else "NOT TRUSTWORTHY"
    items = "".join(f"<li>{escape(r)}</li>" for r in reasons)
    body = (f"<p id='verdict'>{verdict}</p><p>Mode: {escape(mode)} | Claim tier: {escape(tier)} | Market: {escape(market_status)}"
            f" | Broker: {escape(broker)} | Feed: {'OK' if feed_ok else 'DEGRADED'} | Risk gates: "
            f"{'CLEAR' if risk_gates_clear else 'LATCHED'}</p><ul>{items}</ul>")
    html = _page("Session landing", body)
    assert_read_only(html)
    return html


def strategy_table(rows: list[dict]) -> str:
    out = []
    for r in rows:
        status: Status = r["status"]
        css = "failed" if status is Status.FAILED else ("promotable" if status in (Status.FROZEN_CHALLENGER, Status.VERIFIED) else "")
        out.append(f"<tr class='{css}'><td>{escape(r['strategy_id'])}</td><td>{escape(r['family'])}</td>"
                   f"<td>v{r['version']}</td><td>{status.value}</td><td>{escape(r['tier'])}</td></tr>")
    html = _page("Strategies", "<table>" + "".join(out) + "</table>")
    assert_read_only(html)
    return html


def signal_panel(signal: dict) -> str:
    required = ("snapshot_sha256", "intended_order", "spread_bps", "volume", "limits", "expiry", "reason_codes")
    missing = [k for k in required if k not in signal]
    if missing:
        raise CockpitError(f"signal trace is missing: {', '.join(missing)}")
    rows = "".join(f"<tr><th>{k}</th><td>{escape(str(signal[k]))}</td></tr>" for k in required)
    html = _page("Signal", f"<table>{rows}</table>")
    assert_read_only(html)
    return html


ORDER_STATES = ("CREATED", "SUBMITTED", "PARTIAL", "FILLED", "UNKNOWN", "CANCELED")


def blotter(orders: list[dict]) -> str:
    """Orders show every state; the Trades section lists only orders with filled quantity."""
    for o in orders:
        if o["state"] not in ORDER_STATES:
            raise CockpitError(f"unknown order state {o['state']}")
    order_rows = "".join(f"<tr><td>{escape(o['id'])}</td><td>{o['state']}</td><td>{o.get('filled', 0)}</td></tr>" for o in orders)
    trades = [o for o in orders if o["state"] in ("FILLED", "PARTIAL") and o.get("filled", 0) > 0]
    trade_rows = "".join(f"<tr><td>{escape(o['id'])}</td><td>{o['filled']}</td></tr>" for o in trades)
    html = _page("Blotter", f"<h2>Orders</h2><table>{order_rows}</table><h2>Trades</h2><table id='trades'>{trade_rows}</table>")
    assert_read_only(html)
    return html


def pnl_table(gross: Decimal, fees: Decimal, slippage: Decimal, net: Decimal, benchmark: Decimal, ledger_net: Decimal) -> str:
    if net != gross - fees - slippage:
        raise CockpitError("net does not equal gross minus fees and slippage")
    if net != ledger_net:
        raise CockpitError("table does not reconcile to the ledger")
    html = _page("After-cost P&L", f"<table><tr><th>Gross</th><td>{gross}</td></tr><tr><th>Fees and taxes</th><td>{fees}</td></tr>"
                 f"<tr><th>Slippage</th><td>{slippage}</td></tr><tr><th>Net</th><td>{net}</td></tr>"
                 f"<tr><th>Benchmark</th><td>{benchmark}</td></tr><tr><th>Net alpha</th><td>{net - benchmark}</td></tr></table>")
    assert_read_only(html)
    return html


def timeline(scheduled: list[str], outcomes: dict[str, str]) -> str:
    """Every scheduled session appears, including those with no trade or no record."""
    rows = "".join(f"<tr class='{'notrade' if outcomes.get(s) in ('NO_SIGNAL','ABSTAINED') else ''}'><td>{escape(s)}</td>"
                   f"<td>{escape(outcomes.get(s, 'UNRECORDED'))}</td></tr>" for s in scheduled)
    html = _page("Session timeline", f"<p>Scheduled: {len(scheduled)}</p><table>{rows}</table>")
    assert_read_only(html)
    return html


def scorecard(rows: list[dict]) -> str:
    horizons = {r["horizon_sessions"] for r in rows}
    if len(horizons) > 1:
        raise CockpitError("NOT_COMPARABLE: strategies with different horizons cannot be ranked together")
    ordered = sorted(rows, key=lambda r: r["net_alpha_lcb"], reverse=True)
    body = "".join(f"<tr><td>{escape(r['strategy_id'])}</td><td>{r['net_alpha_lcb']}</td><td>{escape(str(r['capacity']))}</td></tr>"
                   for r in ordered)
    html = _page("Scorecard", f"<table>{body}</table>")
    assert_read_only(html)
    return html


def evidence_links(index: dict[str, str]) -> str:
    rows = "".join(f"<tr><td>{escape(n)}</td><td>{escape(h)}</td></tr>" for n, h in sorted(index.items()))
    html = _page("Evidence bundle", f"<table>{rows}</table>")
    assert_read_only(html)
    return html


OPERATOR_DECISIONS = ("CONTINUE_PAPER", "PAUSE", "INVESTIGATE", "REJECT", "REQUEST_REVIEW")


def decisions_panel(live_locked_reasons: list[str]) -> str:
    opts = "".join(f"<li>{d}</li>" for d in OPERATOR_DECISIONS)
    why = "".join(f"<li>{escape(r)}</li>" for r in live_locked_reasons) or "<li>No recorded reason</li>"
    html = _page("Operator decisions", f"<ul id='options'>{opts}</ul><h2>Live mode is LOCKED because</h2><ul>{why}</ul>")
    assert_read_only(html)
    return html

