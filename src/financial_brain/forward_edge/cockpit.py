"""Trader cockpit (FE-0114 .. FE-0123): static, read-only HTML pages generated from data. No forms, no actions.

Every number shown is passed in by the caller; this module never fetches, never submits and never places an order.
Pages carry the claim tier and the reasons live mode is locked.
"""
from __future__ import annotations

import re
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from html import escape
from pathlib import Path

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


def strategy_comparison(rows: list[dict]) -> str:
    """Compare fully costed outcomes while keeping failed and incomplete rows visible."""
    columns = ("strategy_id", "status", "gross", "costs", "net", "benchmark_net", "alpha",
               "ci_lower", "ci_upper", "drawdown", "turnover", "fidelity", "sample")
    rendered: list[tuple[int, Decimal, str, str]] = []
    failed_statuses = {"FAILED", "INSUFFICIENT", "EXTERNAL_BLOCKED", "NOT_VERIFIED", "NO_TRADE"}

    def number(row: dict, key: str) -> Decimal | None:
        value = row.get(key)
        if value is None:
            return None
        try:
            parsed = Decimal(str(value))
        except Exception as exc:
            raise CockpitError(f"{key} must be a finite number") from exc
        if not parsed.is_finite():
            raise CockpitError(f"{key} must be a finite number")
        return parsed

    for row in rows:
        status = str(row.get("status") or "INCOMPLETE").upper()
        sample = row.get("sample")
        sufficient = row.get("sufficient") is True
        if type(sample) is not int or sample <= 0:
            status = "INSUFFICIENT" if status not in failed_statuses else status
        elif row.get("sufficient") is False:
            status = "INSUFFICIENT"
        gross, costs, net = (number(row, key) for key in ("gross", "costs", "net"))
        benchmark, alpha = (number(row, key) for key in ("benchmark_net", "alpha"))
        lower, upper = (number(row, key) for key in ("ci_lower", "ci_upper"))
        if gross is not None and costs is not None and net is not None and net != gross - costs:
            raise CockpitError("strategy comparison net must equal gross minus costs")
        if net is not None and benchmark is not None and alpha is not None and alpha != net - benchmark:
            raise CockpitError("strategy comparison alpha must equal net minus benchmark net")
        if lower is not None and upper is not None and lower > upper:
            raise CockpitError("confidence interval lower bound exceeds upper bound")
        if not sufficient and status not in failed_statuses:
            status = "INSUFFICIENT"
        rank = 0 if status in failed_statuses else 1
        sortable = -alpha if alpha is not None else Decimal("Infinity")
        cells = [escape(str(row.get("strategy_id") or "UNKNOWN")), escape(status)]
        for key in columns[2:]:
            value = row.get(key)
            cells.append(escape("UNKNOWN" if value is None else str(value)))
        klass = "failed" if status in failed_statuses else ""
        rendered.append((rank, sortable, str(row.get("strategy_id") or ""),
                         f"<tr class='{klass}'><td>" + "</td><td>".join(cells) + "</td></tr>"))

    rendered.sort(key=lambda item: (item[0], item[1], item[2]))
    headers = "".join(f"<th>{escape(key.replace('_', ' ').title())}</th>" for key in columns)
    body = "".join(item[3] for item in rendered)
    html = _page("Strategy comparison", f"<p>All {len(rows)} registered outcomes are shown; failed and insufficient rows remain visible.</p>"
                 f"<table><thead><tr>{headers}</tr></thead><tbody>{body}</tbody></table>")
    assert_read_only(html)
    return html


def research_vs_champions(*, provisional: list[dict], forward_champions: list[dict]) -> str:
    """Separate diagnostic research ranks from independently evidenced executable champions."""
    def rank_key(row: dict) -> tuple[Decimal, str]:
        try:
            score = Decimal(str(row.get("ranking_score")))
        except Exception:
            score = Decimal("-Infinity")
        if not score.is_finite():
            score = Decimal("-Infinity")
        return -score, str(row.get("strategy_id") or "")

    ranked = sorted(provisional, key=rank_key)
    research_rows = "".join(
        f"<tr><td>{index}</td><td>{escape(str(row.get('strategy_id') or 'UNKNOWN'))}</td>"
        f"<td>{escape(str(row.get('ranking_score', 'UNKNOWN')))}</td>"
        f"<td>{escape(str(row.get('horizon_sessions', 'UNKNOWN')))}</td>"
        f"<td>{escape(str(row.get('sample', 'UNKNOWN')))}</td><td>PROVISIONAL — RESEARCH ONLY</td></tr>"
        for index, row in enumerate(ranked, 1)
    )
    champion_rows = []
    for row in forward_champions:
        status = str(row.get("status", "UNKNOWN")).upper()
        sessions, receipts = row.get("verified_sessions"), row.get("receipt_sessions")
        digest = str(row.get("evidence_sha256", ""))
        verified = (status == "VERIFIED" and row.get("executable_forward") is True
                    and type(sessions) is int and sessions > 0 and type(receipts) is int and receipts == sessions
                    and bool(re.fullmatch(r"[0-9a-f]{64}", digest)))
        label = "VERIFIED EXECUTABLE FORWARD CHAMPION" if verified else "NOT A VERIFIED EXECUTABLE CHAMPION"
        klass = "promotable" if verified else "failed"
        champion_rows.append(
            f"<tr class='{klass}'><td>{escape(str(row.get('strategy_id') or 'UNKNOWN'))}</td>"
            f"<td>{escape(label)}</td><td>{escape(str(sessions if sessions is not None else 'UNKNOWN'))}</td>"
            f"<td>{escape(str(receipts if receipts is not None else 'UNKNOWN'))}</td>"
            f"<td>{escape(digest or 'MISSING')}</td></tr>"
        )
    html = _page("Research and forward champion status",
                 "<h2>Provisional research ranking</h2><p>Exploratory diagnostic only. No row in this section is a tradable edge or champion.</p>"
                 f"<table><thead><tr><th>Rank</th><th>Strategy</th><th>Diagnostic score</th><th>Horizon sessions</th>"
                 f"<th>Sample</th><th>Status</th></tr></thead><tbody>{research_rows}</tbody></table>"
                 "<h2>Verified executable forward champions</h2><p>Only rows with VERIFIED status, executable-forward attestation,"
                 " complete session receipt coverage, and a valid evidence digest can receive this label.</p>"
                 "<table><thead><tr><th>Strategy</th><th>Status</th><th>Verified sessions</th><th>Receipt sessions</th>"
                 f"<th>Evidence SHA-256</th></tr></thead><tbody>{''.join(champion_rows)}</tbody></table>")
    assert_read_only(html)
    return html


def profitability_evidence(rows: list[dict]) -> str:
    """Require uncertainty, multiplicity, matched benchmark, and capacity beside any edge claim."""
    def decimal_or_none(value: object) -> Decimal | None:
        if value is None:
            return None
        try:
            result = Decimal(str(value))
        except Exception:
            return None
        return result if result.is_finite() else None

    rendered = []
    for row in rows:
        lower = decimal_or_none(row.get("ci_lower"))
        upper = decimal_or_none(row.get("ci_upper"))
        ci_level = decimal_or_none(row.get("ci_level_pct"))
        net_lcb = decimal_or_none(row.get("net_alpha_lcb"))
        benchmark_net = decimal_or_none(row.get("matched_benchmark_net_alpha"))
        capacity = decimal_or_none(row.get("estimated_capacity_inr"))
        multiplicity = row.get("multiplicity_count")
        adjusted_alpha = decimal_or_none(row.get("familywise_adjusted_alpha"))
        missing = []
        if lower is None or upper is None or lower > upper:
            missing.append("valid confidence interval")
        if ci_level is None or not Decimal("50") < ci_level < Decimal("100"):
            missing.append("confidence interval level")
        if net_lcb is None:
            missing.append("net-alpha lower bound")
        if type(multiplicity) is not int or multiplicity < 1:
            missing.append("multiplicity count")
        if adjusted_alpha is None or adjusted_alpha <= 0:
            missing.append("familywise-adjusted alpha")
        if not row.get("matched_benchmark_id") or benchmark_net is None:
            missing.append("matched benchmark and outcome")
        if capacity is None or capacity <= 0:
            missing.append("positive estimated capacity")
        adjusted = row.get("familywise_adjusted") is True
        if not adjusted:
            missing.append("verified multiplicity adjustment")
        positive = not missing and lower is not None and lower > 0 and net_lcb is not None and net_lcb > 0
        status = ("POSITIVE ADJUSTED LOWER BOUND — NOT A GUARANTEE" if positive
                  else "INSUFFICIENT / NON-POSITIVE EVIDENCE")
        detail = "; ".join(missing) if missing else "all required evidence fields present"
        cells = (
            row.get("strategy_id", "UNKNOWN"), status,
            row.get("claim", "No claim supplied"),
            row.get("ci_level_pct", "UNKNOWN"), row.get("ci_lower", "UNKNOWN"), row.get("ci_upper", "UNKNOWN"),
            row.get("net_alpha_lcb", "UNKNOWN"), row.get("multiplicity_count", "UNKNOWN"),
            row.get("familywise_adjusted_alpha", "UNKNOWN"),
            row.get("matched_benchmark_id", "UNKNOWN"),
            row.get("matched_benchmark_net_alpha", "UNKNOWN"),
            row.get("estimated_capacity_inr", "UNKNOWN"),
            row.get("win_rate", "UNKNOWN"), row.get("total_net_rupees", "UNKNOWN"), detail,
        )
        rendered.append((0 if status.startswith("INSUFFICIENT") else 1,
                         "<tr class='failed'><td>" + "</td><td>".join(escape(str(value)) for value in cells)
                         + "</td></tr>"))
    rendered.sort(key=lambda item: item[0])
    headers = ("Strategy", "Evidence status", "Claim", "CI level %", "CI lower", "CI upper", "Net alpha LCB",
               "Multiplicity count", "Adjusted alpha", "Matched benchmark", "Benchmark net alpha",
               "Estimated capacity INR", "Win rate (descriptive)", "Total net rupees (descriptive)", "Evidence gaps")
    table = "<table><thead><tr>" + "".join(f"<th>{escape(header)}</th>" for header in headers) + "</tr></thead><tbody>"
    body = "".join(item[1] for item in rendered)
    html = _page("Profitability evidence", "<p>No strategy is labeled proven from win rate or cumulative rupees."
                 " A positive adjusted lower bound is still uncertain and conditional on the stated evidence.</p>"
                 f"{table}{body}</tbody></table>")
    assert_read_only(html)
    return html


def portfolio_nav(archives: list[dict], canonical_nav_rows: list[dict]) -> str:
    """Render cash plus marked open positions, verifying every session against the account NAV journal."""
    if not archives:
        raise CockpitError("portfolio NAV needs at least one archived session")

    def canonical(value: object) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")

    def money(value: object, label: str) -> Decimal:
        try:
            parsed = Decimal(str(value))
        except Exception as exc:
            raise CockpitError(f"{label} must be a finite decimal") from exc
        if not parsed.is_finite():
            raise CockpitError(f"{label} must be a finite decimal")
        return parsed

    ledger_by_date: dict[str, dict] = {}
    for row in canonical_nav_rows:
        day = row.get("session_date")
        if not isinstance(day, str) or day in ledger_by_date:
            raise CockpitError("canonical NAV ledger has a missing or duplicate session date")
        ledger_by_date[day] = row
    archive_by_date: dict[str, dict] = {}
    for original in archives:
        row = dict(original)
        for external in ("archive_path", "archive_sha256", "next_open_reconciliation"):
            row.pop(external, None)
        day = row.get("session_date")
        if not isinstance(day, str) or day in archive_by_date:
            raise CockpitError("NAV archive has a missing or duplicate session date")
        claimed = row.get("snapshot_sha256")
        unsigned = {key: value for key, value in row.items() if key != "snapshot_sha256"}
        if not isinstance(claimed, str) or hashlib.sha256(canonical(unsigned)).hexdigest() != claimed:
            raise CockpitError(f"NAV archive hash is invalid for {day}")
        inventory = row.get("inventory")
        if not isinstance(inventory, list):
            raise CockpitError(f"NAV inventory is missing for {day}")
        inventory_hash = hashlib.sha256(canonical(inventory)).hexdigest()
        if row.get("inventory_sha256") != inventory_hash:
            raise CockpitError(f"NAV inventory hash is invalid for {day}")
        cash, equity = money(row.get("cash"), "cash"), money(row.get("equity"), "equity")
        open_value = sum((money(item.get("market_value"), "position market value")
                          for item in inventory if isinstance(item, dict)), Decimal(0))
        if len(inventory) != sum(isinstance(item, dict) for item in inventory):
            raise CockpitError(f"NAV inventory row is malformed for {day}")
        if equity != cash + open_value:
            raise CockpitError(f"NAV does not reconcile to cash and open positions for {day}")
        snapshot = row.get("snapshot")
        ledger = ledger_by_date.get(day)
        if ledger is None:
            raise CockpitError(f"NAV session coverage mismatch: canonical ledger row is missing for {day}")
        if not isinstance(snapshot, dict) or snapshot != ledger:
            raise CockpitError(f"dashboard NAV does not reproduce the canonical ledger row for {day}")
        if money(snapshot.get("nav"), "canonical NAV") != equity:
            raise CockpitError(f"canonical ledger NAV differs from marked equity for {day}")
        archive_by_date[day] = {"cash": cash, "open_value": open_value, "nav": equity,
                                "external_flow": money(snapshot.get("external_flow"), "external flow"),
                                "snapshot_sha256": claimed}
    if set(archive_by_date) != set(ledger_by_date):
        missing_archives = sorted(set(ledger_by_date) - set(archive_by_date))
        missing_ledger = sorted(set(archive_by_date) - set(ledger_by_date))
        raise CockpitError(f"NAV session coverage mismatch: missing archives={missing_archives}, missing ledger={missing_ledger}")
    rows = []
    for day, values in sorted(archive_by_date.items()):
        rows.append("<tr>" + "</td><td>".join(escape(str(value)) for value in (
            day, values["cash"], values["open_value"], values["nav"], values["external_flow"],
            values["snapshot_sha256"])) + "</td></tr>")
    table = ("<table><thead><tr><th>Session</th><th>Cash</th><th>Open positions</th>"
             "<th>Portfolio NAV</th><th>External flow</th><th>Archive SHA-256</th></tr></thead><tbody>"
             + "".join(rows) + "</tbody></table>")
    html = _page("Portfolio NAV", f"<p>Cash plus marked open positions; all {len(rows)} sessions reconcile to canonical ledger snapshots.</p>{table}")
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


def execution_fill_table(receipts: list[dict]) -> str:
    """Show execution nature and its source inputs; simulated fills are never called exchange fills."""
    sha_pattern = re.compile(r"^[0-9a-f]{64}$")
    rows: list[str] = []
    for receipt in receipts:
        receipt_id = str(receipt.get("receipt_id") or "UNKNOWN")
        fills = receipt.get("fills") or []
        if not isinstance(fills, list):
            raise CockpitError("execution receipt fills must be a list")
        if not fills:
            rows.append(f"<tr><td>{escape(receipt_id)}</td><td>NO FILL</td><td>—</td>"
                        "<td>Receipt has no fill. No execution is implied.</td></tr>")
            continue
        for fill in fills:
            if not isinstance(fill, dict):
                raise CockpitError("execution fill must be an object")
            fidelity = str(receipt.get("fidelity", "UNKNOWN")).upper()
            inputs: dict = {}
            if fidelity == "SIMULATED_QUOTE":
                observation = fill.get("quote_observation")
                required_quote = {"instrument", "exchange", "source", "event_utc", "received_utc",
                                  "available_utc", "bid_price", "ask_price", "bid_qty", "ask_qty",
                                  "quote_event_sha256"}
                complete_quote = isinstance(observation, dict) and required_quote.issubset(observation)
                valid_model = (isinstance(fill.get("execution_model_id"), str)
                               and bool(fill.get("execution_model_id"))
                               and bool(sha_pattern.fullmatch(str(fill.get("execution_model_sha256", ""))))
                               and bool(sha_pattern.fullmatch(str(fill.get("fee_schedule_sha256", ""))))
                               and bool(sha_pattern.fullmatch(str(fill.get("latency_model_sha256", ""))))
                               and bool(sha_pattern.fullmatch(str(fill.get("data_snapshot_sha256", "")))))
                nature = ("SIMULATED ONLY — NOT AN EXCHANGE FILL" if complete_quote and valid_model
                          else "SIMULATION EVIDENCE INCOMPLETE — NOT AN EXCHANGE FILL")
                inputs = {"execution_model_id": fill.get("execution_model_id"),
                          "execution_model_sha256": fill.get("execution_model_sha256"),
                          "fee_schedule_sha256": fill.get("fee_schedule_sha256"),
                          "latency_model_sha256": fill.get("latency_model_sha256"),
                          "data_snapshot_sha256": fill.get("data_snapshot_sha256"),
                          "quote_observation": observation}
            elif fidelity in {"OBSERVED_BROKER", "OBSERVED_EXCHANGE"}:
                required_observed = ("provider_fill_id", "source", "exchange", "observed_at_utc",
                                     "source_sha256", "broker_session_id")
                complete_observed = (fill.get("execution_nature") == "OBSERVED"
                                     and fill.get("reconciled") is True
                                     and all(fill.get(key) for key in required_observed)
                                     and bool(sha_pattern.fullmatch(str(fill.get("source_sha256", "")))))
                nature = ("OBSERVED READ-ONLY FILL" if complete_observed
                          else "OBSERVED LABEL UNVERIFIED — DO NOT TREAT AS EXCHANGE FILL")
                inputs = {key: fill.get(key) for key in required_observed}
                inputs["reconciled"] = fill.get("reconciled")
            else:
                nature = "UNCLASSIFIED — NOT VERIFIED AS AN EXCHANGE FILL"
                inputs = {"fidelity": fidelity, "execution_nature": fill.get("execution_nature")}
            input_text = escape(json.dumps(inputs, sort_keys=True, separators=(",", ":"), default=str))
            details = " | ".join(str(fill.get(key, "UNKNOWN")) for key in ("quantity", "price"))
            rows.append(f"<tr><td>{escape(receipt_id)}</td><td>{escape(nature)}</td>"
                        f"<td>{escape(str(fill.get('fill_id') or 'UNKNOWN'))}; {escape(details)}</td>"
                        f"<td><code>{input_text}</code></td></tr>")
    html = _page("Execution evidence by fill", "<p>Simulated quote fills are model outputs, never observed exchange executions.</p>"
                 "<table><thead><tr><th>Receipt</th><th>Execution label</th><th>Fill ID; qty / price</th>"
                 f"<th>Inputs and provenance</th></tr></thead><tbody>{''.join(rows)}</tbody></table>")
    assert_read_only(html)
    return html


def trade_drilldown(*, trade_id: str, events: list[dict]) -> str:
    """Render source-to-attribution evidence only when every stage binds to a verified journal record."""
    required_stages = ("source_quote", "signal", "risk", "intent", "fill", "ledger", "exit", "attribution")
    if not isinstance(trade_id, str) or not trade_id.strip():
        raise CockpitError("trade drilldown needs a stable trade ID")
    if not isinstance(events, list):
        raise CockpitError("trade drilldown events must be a list")

    def canon(value: object) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")

    mapped: dict[str, dict] = {}
    for event in events:
        stage = event.get("stage")
        if stage not in required_stages or stage in mapped:
            raise CockpitError("trade drilldown has an unknown or duplicate evidence stage")
        journal_id, claimed, record = event.get("journal_id"), event.get("record_sha256"), event.get("record")
        if not isinstance(journal_id, str) or not journal_id.strip() or not isinstance(record, dict):
            raise CockpitError(f"{stage} must link to a stable journal ID and record")
        if not isinstance(claimed, str) or not re.fullmatch(r"[0-9a-f]{64}", claimed):
            raise CockpitError(f"{stage} journal record needs a SHA-256")

        if {"seq", "prev", "hash", "rec"}.issubset(record):
            payload = record["rec"]
            try:
                expected = hashlib.sha256((record["prev"] + canon(payload).decode("utf-8")).encode("utf-8")).hexdigest()
            except (TypeError, ValueError) as exc:
                raise CockpitError(f"{stage} durable journal record is invalid") from exc
            valid = record.get("hash") == expected == claimed
        elif "snapshot_sha256" in record:
            unsigned = {key: value for key, value in record.items() if key != "snapshot_sha256"}
            expected = hashlib.sha256(canon(unsigned)).hexdigest()
            payload, valid = record, record.get("snapshot_sha256") == expected == claimed
        else:
            payload = record
            valid = hashlib.sha256(canon(payload)).hexdigest() == claimed
        if not valid:
            raise CockpitError(f"{stage} journal record hash does not match")
        fields = event.get("fields", payload)
        if not isinstance(fields, dict) or any(key not in payload or payload[key] != value for key, value in fields.items()):
            raise CockpitError(f"{stage} displayed fields do not match the immutable journal record")
        mapped[stage] = {"journal_id": journal_id, "record_sha256": claimed,
                         "record": record, "fields": fields}

    missing = [stage for stage in required_stages if stage not in mapped]
    if missing:
        raise CockpitError(f"trade drilldown is missing journal-linked stages: {', '.join(missing)}")
    rows = []
    for stage in required_stages:
        event = mapped[stage]
        anchor = f"record-{event['record_sha256']}"
        link = f"<a href='#{anchor}'>{escape(event['journal_id'])}</a>"
        for key, value in sorted(event["fields"].items()):
            shown = escape(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str))
            rows.append(f"<tr><th>{escape(stage.replace('_', ' ').title())} · {escape(str(key))}</th>"
                        f"<td><a href='#{anchor}'>{shown}</a></td><td>{link}</td></tr>")
        if not event["fields"]:
            rows.append(f"<tr><th>{escape(stage.replace('_', ' ').title())}</th><td>Record present</td><td>{link}</td></tr>")
    details = []
    for stage in required_stages:
        event = mapped[stage]
        anchor = f"record-{event['record_sha256']}"
        raw = escape(json.dumps(event["record"], sort_keys=True, indent=2, default=str))
        details.append(f"<section id='{anchor}'><h3>{escape(stage)} · {escape(event['journal_id'])}</h3>"
                       f"<p>SHA-256: {event['record_sha256']}</p><pre>{raw}</pre></section>")
    html = _page(f"Trade drilldown {trade_id}", f"<p>Trade: {escape(trade_id)}. Every field links to its hash-verified immutable journal record.</p>"
                 "<table><thead><tr><th>Stage / field</th><th>Value</th><th>Journal ID</th></tr></thead><tbody>"
                 + "".join(rows) + "</tbody></table><h2>Journal records</h2>" + "".join(details))
    assert_read_only(html)
    return html


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


def opportunity_coverage(*, scheduled_dates: list[str], outcomes: list[dict]) -> str:
    """Count explicit opportunity outcomes by reason without dropping unrecorded planned dates."""
    from collections import Counter

    if not scheduled_dates or len(set(scheduled_dates)) != len(scheduled_dates):
        raise CockpitError("scheduled dates must be non-empty and unique")
    allowed = {"REJECTED", "ABSTAINED", "MISSED", "NO_SIGNAL", "TRADED", "UNKNOWN"}
    planned = set(scheduled_dates)
    seen_ids: set[str] = set()
    by_date: dict[str, list[dict]] = {day: [] for day in scheduled_dates}
    for event in outcomes:
        event_id = event.get("event_id")
        day = event.get("session_date")
        kind = str(event.get("outcome", "")).upper()
        reason = event.get("reason_code")
        if not isinstance(event_id, str) or not event_id.strip() or event_id in seen_ids:
            raise CockpitError("opportunity outcome IDs must be non-empty and unique")
        if day not in planned:
            raise CockpitError("opportunity outcome falls outside the planned session denominator")
        if kind not in allowed:
            raise CockpitError(f"unknown opportunity outcome {kind or 'MISSING'}")
        if not isinstance(reason, str) or not reason.strip():
            raise CockpitError("every opportunity outcome needs an explicit reason code")
        seen_ids.add(event_id)
        by_date[day].append({**event, "outcome": kind, "reason_code": reason.strip()})

    counts = Counter(str(event["outcome"]) for events in by_date.values() for event in events)
    unrecorded = [day for day, events in by_date.items() if not events]
    counts["UNRECORDED"] = len(unrecorded)
    count_html = "".join(f"<tr><th>{escape(kind.replace('_', ' ').title())}</th><td>{counts[kind]}</td></tr>"
                         for kind in ("REJECTED", "ABSTAINED", "MISSED", "NO_SIGNAL", "TRADED", "UNKNOWN", "UNRECORDED"))
    rows: list[str] = []
    for day in sorted(planned):
        events = sorted(by_date[day], key=lambda item: str(item["event_id"]))
        if not events:
            events = [{"event_id": "—", "outcome": "UNRECORDED", "reason_code": "NO_RECORDED_OUTCOME"}]
        rows.extend(
            f"<tr><td>{escape(day)}</td><td>{escape(str(event['event_id']))}</td>"
            f"<td>{escape(str(event['outcome']))}</td><td>{escape(str(event['reason_code']))}</td></tr>"
            for event in events
        )
    html = _page("Opportunity coverage", f"<p>Planned session denominator: {len(planned)} dates."
                 f" Unrecorded dates: {len(unrecorded)}.</p><h2>Outcome counts</h2>"
                 f"<table><tbody>{count_html}</tbody></table><h2>All planned dates and outcomes</h2>"
                 "<table><thead><tr><th>Session</th><th>Event</th><th>Outcome</th><th>Reason code</th></tr></thead><tbody>"
                 + "".join(rows) + "</tbody></table>")
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


def risk_incident_review(journal_path: Path) -> str:
    """Review persistent paper-account latches from a chain-verified account journal."""
    from financial_brain.forward_edge.durable import DurableJournal
    from financial_brain.forward_edge.approvals import is_agent_actor

    try:
        records = DurableJournal(Path(journal_path)).records()
    except Exception as exc:
        raise CockpitError("risk incident journal is missing, corrupt, or unreadable") from exc
    active: dict | None = None
    incidents: list[dict] = []
    for record in records:
        if record.get("t") != "RISK_LATCH":
            continue
        state = record.get("state")
        if state == "TRIPPED":
            if active is not None:
                raise CockpitError("risk incident journal contains overlapping active latches")
            incident = record.get("incident_id")
            reason = record.get("reason")
            if not isinstance(incident, str) or not incident or not isinstance(reason, str) or not reason:
                raise CockpitError("risk latch trip record lacks incident identity or reason")
            active = record
            incidents.append({"trip": record, "reset": None})
        elif state == "RESET":
            if active is None or record.get("incident_id") != active.get("incident_id"):
                raise CockpitError("risk latch reset does not match an active incident")
            actor, digest = record.get("actor"), record.get("signoff_sha256")
            if (not isinstance(actor, str) or not actor.strip() or is_agent_actor(actor, "human")
                    or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                raise CockpitError("risk reset record lacks human hash-backed sign-off")
            incidents[-1]["reset"] = record
            active = None
        else:
            raise CockpitError("risk incident journal contains an unknown latch state")
    rows = []
    for incident in incidents:
        trip, reset = incident["trip"], incident["reset"]
        status = "HALT ACTIVE — NEW PAPER INTENTS BLOCKED" if reset is None else "RESET BY HUMAN SIGN-OFF"
        details = [
            f"Incident {trip['incident_id']}", f"Reason {trip['reason']}",
            f"Occurred {trip.get('occurred_at', 'UNKNOWN')}",
        ]
        if reset:
            details.extend([f"Repair {reset.get('repair_id', 'UNKNOWN')}",
                            f"Actor {reset.get('actor', 'UNKNOWN')}",
                            f"Signed {reset.get('signed_at', 'UNKNOWN')}",
                            f"Sign-off SHA-256 {reset.get('signoff_sha256', 'UNKNOWN')}"])
        rows.append(f"<tr class='{'failed' if reset is None else ''}'><td>{escape(status)}</td>"
                    f"<td>{escape(' · '.join(details))}</td></tr>")
    current = "HALT ACTIVE — PAPER INTENTS BLOCKED" if active else "NO ACTIVE PAPER RISK LATCH"
    reset_rule = "Reset is an account-journal operation requiring a human, timestamped, hash-backed repair sign-off. "
    reset_rule += "This page is read-only; it cannot reset a latch or submit an order."
    table_rows = "".join(rows) or "<tr><td colspan='2'>No incidents recorded</td></tr>"
    html = _page("Paper risk incident review", f"<p class='{'failed' if active else 'notrade'}' id='risk-state'>{current}</p>"
                 f"<p>Paper account only. {escape(reset_rule)}</p>"
                 "<table><thead><tr><th>State</th><th>Incident record</th></tr></thead><tbody>"
                 f"{table_rows}</tbody></table>")
    assert_read_only(html)
    return html
