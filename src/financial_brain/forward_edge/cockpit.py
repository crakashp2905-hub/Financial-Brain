"""Trader cockpit (FE-0114 .. FE-0123): static, read-only HTML pages generated from data. No forms, no actions.

Every number shown is passed in by the caller; this module never fetches, never submits and never places an order.
Pages carry the claim tier and the reasons live mode is locked.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from html import escape

from financial_brain.forward_edge.registry import Status


class CockpitError(ValueError):
    pass


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


# ---- FE-0114 ----

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


def session_overview(*, now: datetime, feed: dict, calendar: dict,
                     active_version: dict | None, broker: dict,
                     risk_latches: list[str]) -> str:
    """Render a fail-closed session overview; stale or incomplete evidence blocks readiness."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise CockpitError("overview clock must be timezone-aware")
    now_utc = now.astimezone(timezone.utc)
    blockers: list[str] = []

    def fresh(item: dict, timestamp_key: str, max_age_key: str, label: str) -> tuple[bool, str]:
        stamp = item.get(timestamp_key)
        age_limit = item.get(max_age_key)
        try:
            observed = datetime.fromisoformat(stamp) if isinstance(stamp, str) else None
            if observed is None or observed.tzinfo is None or observed.utcoffset() is None:
                raise ValueError
            if type(age_limit) is not int or age_limit < 0:
                raise ValueError
            age = (now_utc - observed.astimezone(timezone.utc)).total_seconds()
        except (TypeError, ValueError):
            blockers.append(f"{label}_EVIDENCE_MISSING_OR_INVALID")
            return False, "MISSING / INVALID"
        if age < 0:
            blockers.append(f"{label}_TIMESTAMP_IN_FUTURE")
            return False, "FUTURE TIMESTAMP"
        if age > age_limit:
            blockers.append(f"{label}_STALE")
            return False, f"STALE ({int(age)}s; limit {age_limit}s)"
        return True, f"FRESH ({int(age)}s)"

    feed_fresh, feed_state = fresh(feed, "last_received_utc", "max_age_seconds", "FEED")
    feed_complete = feed.get("complete") is True
    if not feed_complete:
        blockers.append("FEED_INCOMPLETE")
        feed_state = "INCOMPLETE / " + feed_state
    if not feed.get("source") or not feed.get("exchange"):
        blockers.append("FEED_PROVENANCE_MISSING")
        feed_state = "INCOMPLETE PROVENANCE / " + feed_state

    calendar_fresh, calendar_state = fresh(
        calendar, "checked_at_utc", "max_age_seconds", "CALENDAR")
    if calendar.get("is_session") is not True:
        blockers.append("CALENDAR_NOT_CONFIRMED_TRADING_SESSION")
        calendar_state = "CLOSED / UNKNOWN / " + calendar_state
    if (not calendar.get("version")
            or not re.fullmatch(r"[0-9a-f]{64}", str(calendar.get("evidence_sha256", "")))):
        blockers.append("CALENDAR_PROVENANCE_MISSING")
        calendar_state = "INCOMPLETE PROVENANCE / " + calendar_state

    version_ok = bool(active_version and active_version.get("strategy_id")
                      and active_version.get("version")
                      and re.fullmatch(r"[0-9a-f]{64}", str(active_version.get("artifact_sha256", ""))))
    if not version_ok:
        blockers.append("ACTIVE_STRATEGY_VERSION_MISSING_OR_INVALID")
    version_details = "no valid active version"
    if active_version is not None and version_ok:
        version_details = f"{active_version.get('strategy_id')} v{active_version.get('version')}"

    broker_fresh, broker_state = fresh(
        broker, "observed_at_utc", "max_age_seconds", "BROKER_READ_ONLY")
    broker_evidence_valid = bool(re.fullmatch(r"[0-9a-f]{64}", str(broker.get("evidence_sha256", ""))))
    if broker.get("read_only") is not True or not broker.get("session_id") or not broker_evidence_valid:
        blockers.append("BROKER_READ_ONLY_EVIDENCE_INCOMPLETE")
        broker_state = "INCOMPLETE / " + broker_state

    normalized_latches = [str(latch).strip() for latch in risk_latches if str(latch).strip()]
    if normalized_latches:
        blockers.extend(f"RISK_LATCH:{latch}" for latch in normalized_latches)

    ready = (feed_fresh and feed_complete and calendar_fresh and calendar.get("is_session") is True
             and version_ok and broker_fresh and broker.get("read_only") is True
             and bool(broker.get("session_id")) and broker_evidence_valid
             and not normalized_latches and not blockers)
    verdict = "PAPER SESSION READY" if ready else "NOT READY — TRADING BLOCKED"
    rows = [
        ("Feed", feed_state, f"source={feed.get('source') or 'UNKNOWN'}; exchange={feed.get('exchange') or 'UNKNOWN'}"),
        ("Market calendar", calendar_state, f"session={calendar.get('is_session', 'UNKNOWN')}; version={calendar.get('version') or 'UNKNOWN'}"),
        ("Active strategy", "FROZEN" if version_ok else "MISSING / INVALID", version_details),
        ("Broker session", broker_state, f"read-only={broker.get('read_only', 'UNKNOWN')}; session={broker.get('session_id') or 'UNKNOWN'}"),
        ("Risk latches", "LATCHED" if normalized_latches else "CLEAR",
         ", ".join(normalized_latches) if normalized_latches else "none recorded"),
    ]
    row_html = "".join(f"<tr><th>{escape(name)}</th><td>{escape(state)}</td><td>{escape(detail)}</td></tr>"
                       for name, state, detail in rows)
    blocker_html = "".join(f"<li>{escape(reason)}</li>" for reason in sorted(set(blockers)))
    severity = "promotable" if ready else "failed"
    html = _page("Session overview", f"<p class='{severity}' id='readiness'>{verdict}</p>"
                 f"<table><tr><th>Control</th><th>Status</th><th>Details</th></tr>{row_html}</table>"
                 f"<h2>Readiness blockers</h2><ul>{blocker_html or '<li>None</li>'}</ul>")
    assert_read_only(html)
    return html


# ---- FE-0115 ----

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


# ---- FE-0116 ----

def signal_panel(signal: dict) -> str:
    required = ("snapshot_sha256", "intended_order", "spread_bps", "volume", "limits", "expiry", "reason_codes")
    missing = [k for k in required if k not in signal]
    if missing:
        raise CockpitError(f"signal trace is missing: {', '.join(missing)}")
    rows = "".join(f"<tr><th>{k}</th><td>{escape(str(signal[k]))}</td></tr>" for k in required)
    html = _page("Signal", f"<table>{rows}</table>")
    assert_read_only(html)
    return html


# ---- FE-0117 ----

ORDER_STATES = ("CREATED", "SUBMITTED", "PARTIAL", "FILLED", "CANCEL_PENDING",
                "UNKNOWN", "CANCELED", "MISSED", "REJECTED", "NO_TRADE")


def blotter(orders: list[dict], *, updates: list[dict] | None = None) -> str:
    """Show exact requested, filled and remaining quantities; unknown values stay explicit."""
    for o in orders:
        if o["state"] not in ORDER_STATES:
            raise CockpitError(f"unknown order state {o['state']}")

        requested = o.get("requested")
        filled = o.get("filled")
        remaining = o.get("remaining")
        for name, value in (("requested", requested), ("filled", filled), ("remaining", remaining)):
            if value is not None and (type(value) is not int or value < 0):
                raise CockpitError(f"{name} quantity must be a non-negative integer or unknown")
        if requested is not None:
            if filled is None or remaining != requested - filled:
                raise CockpitError("remaining quantity must reconcile to requested minus filled")
        elif remaining is not None:
            raise CockpitError("remaining quantity cannot be shown without requested quantity")

    def shown(value: object) -> str:
        return "UNKNOWN" if value is None else str(value)

    order_rows = "".join(
        f"<tr><td>{escape(o['id'])}</td><td>{escape(o['state'])}</td>"
        f"<td>{shown(o.get('requested'))}</td><td>{shown(o.get('filled'))}</td>"
        f"<td>{shown(o.get('remaining'))}</td><td>{escape(str(o.get('reason') or ''))}</td></tr>"
        for o in orders
    )
    trades = [o for o in orders if o["state"] in ("FILLED", "PARTIAL") and o.get("filled", 0) > 0]
    trade_rows = "".join(
        f"<tr><td>{escape(o['id'])}</td><td>{shown(o.get('filled'))}</td>"
        f"<td>{shown(o.get('remaining'))}</td></tr>" for o in trades
    )
    headers = "<tr><th>Order</th><th>State</th><th>Requested</th><th>Filled</th><th>Remaining</th><th>Reason</th></tr>"
    history_rows = "".join(
        f"<tr><td>{escape(str(row.get('at_utc', 'UNKNOWN')))}</td><td>{escape(str(row.get('order_id', '')))}</td>"
        f"<td>{escape(str(row.get('status', 'UNKNOWN')))}</td>"
        f"<td>{shown(row.get('requested_quantity'))}</td><td>{shown(row.get('filled_quantity'))}</td>"
        f"<td>{shown(row.get('remaining_quantity'))}</td><td>{escape(str(row.get('reason') or ''))}</td></tr>"
        for row in (updates or [])
    )
    history = (f"<h2>Order update history</h2><table><tr><th>Time</th><th>Order</th><th>State</th>"
               f"<th>Requested</th><th>Filled</th><th>Remaining</th><th>Reason</th></tr>{history_rows}</table>"
               if updates else "")
    html = _page("Blotter", f"<h2>Orders</h2><table>{headers}{order_rows}</table>"
                 f"<h2>Trades</h2><table id='trades'><tr><th>Order</th><th>Filled</th><th>Remaining</th></tr>"
                 f"{trade_rows}</table>{history}")
    assert_read_only(html)
    return html


def blotter_from_journal(records: list[dict], *, session_id: str | None = None) -> str:
    """Render the latest exact order state projected from hash-journaled paper updates."""
    from financial_brain.forward_edge.paper_order_lifecycle import project_order_updates

    try:
        orders = project_order_updates(records, session_id=session_id)
    except ValueError as exc:
        raise CockpitError(f"paper order journal cannot be rendered: {exc}") from exc
    updates = [row for row in records if row.get("t") == "PAPER_ORDER_UPDATE"
               and (session_id is None or row.get("session_id") == session_id)]
    return blotter(orders, updates=updates)


# ---- FE-0118 ----

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


# ---- FE-0119 ----

def timeline(scheduled: list[str], outcomes: dict[str, str]) -> str:
    """Every scheduled session appears, including those with no trade or no record."""
    rows = "".join(f"<tr class='{'notrade' if outcomes.get(s) in ('NO_SIGNAL','ABSTAINED') else ''}'><td>{escape(s)}</td>"
                   f"<td>{escape(outcomes.get(s, 'UNRECORDED'))}</td></tr>" for s in scheduled)
    html = _page("Session timeline", f"<p>Scheduled: {len(scheduled)}</p><table>{rows}</table>")
    assert_read_only(html)
    return html


# ---- FE-0120 ----

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


# ---- FE-0121 ----

def evidence_links(index: dict[str, str]) -> str:
    rows = "".join(f"<tr><td>{escape(n)}</td><td>{escape(h)}</td></tr>" for n, h in sorted(index.items()))
    html = _page("Evidence bundle", f"<table>{rows}</table>")
    assert_read_only(html)
    return html


# ---- FE-0123 ----

OPERATOR_DECISIONS = ("CONTINUE_PAPER", "PAUSE", "INVESTIGATE", "REJECT", "REQUEST_REVIEW")


def decisions_panel(live_locked_reasons: list[str]) -> str:
    opts = "".join(f"<li>{d}</li>" for d in OPERATOR_DECISIONS)
    why = "".join(f"<li>{escape(r)}</li>" for r in live_locked_reasons) or "<li>No recorded reason</li>"
    html = _page("Operator decisions", f"<ul id='options'>{opts}</ul><h2>Live mode is LOCKED because</h2><ul>{why}</ul>")
    assert_read_only(html)
    return html
