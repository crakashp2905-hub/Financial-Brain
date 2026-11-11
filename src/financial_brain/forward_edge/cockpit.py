"""Trader cockpit (FE-0114 .. FE-0123): static, read-only HTML pages generated from data. No forms, no actions.

Every number shown is passed in by the caller; this module never fetches, never submits and never places an order.
Pages carry the claim tier and the reasons live mode is locked.
"""
from __future__ import annotations

import re
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
