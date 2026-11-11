"""Static, offline trader views for algorithm research (ALG-074..080, ALG-082, ALG-083).

Everything is rendered to self-contained HTML or SVG strings with escaped text: no scripts, no external resources,
no network. Each view carries the paper-only banner and never shows a source claim as an edge. Confirmed and
unconfirmed states are drawn differently, and the wording never says "buy" or "sell".
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from html import escape
from math import floor
from typing import Any

from financial_brain.patterns.chart_svg import render_candles_svg
from financial_brain.patterns.detectors import Bars

PAPER_BANNER = "PAPER ONLY. SIMULATION AND RESEARCH. No order is sent anywhere. Source claims are not results."

STATE_STYLE = {
    "CONFIRMED": {"dash": "", "opacity": "1.0", "label": "confirmed close beyond the level"},
    "LOCKED": {"dash": "6 3", "opacity": "0.8", "label": "level locked, not yet confirmed"},
    "CANDIDATE": {"dash": "2 3", "opacity": "0.5", "label": "unconfirmed candidate"},
    "INVALIDATED": {"dash": "1 4", "opacity": "0.3", "label": "invalidated"},
    "EXPIRED": {"dash": "1 4", "opacity": "0.3", "label": "expired"},
}
FORBIDDEN_WORDS = ("buy now", "sell now", "guaranteed", "profit target")


def state_style(state: str) -> dict[str, str]:
    """Visual style per state (ALG-077). Unknown states get the faintest style, never a live-looking one."""
    return STATE_STYLE.get(state, STATE_STYLE["CANDIDATE"])


def money(value: float, *, tick: float, lot: int, quantity_lots: int) -> str:
    """Price rounded down to the tick and quantity in whole lots, with INR labelled (ALG-078)."""
    if tick <= 0 or lot < 1 or quantity_lots < 0:
        raise ValueError("tick and lot must be positive and quantity non-negative")
    price = floor(value / tick + 1e-9) * tick
    qty = quantity_lots * lot
    return f"INR {price:,.2f} x {qty} units ({quantity_lots} lot(s) of {lot})"


def overlay_svg(bars: Bars, overlays: Sequence[dict[str, Any]], *, shown_until: int, title: str) -> str:
    """Candles with neckline and breakout overlays. An overlay beyond ``shown_until`` is never drawn (ALG-076)."""
    base = render_candles_svg(bars, title=title)
    pieces: list[str] = []
    n = len(bars)
    for ov in overlays:
        start, end = int(ov["from_index"]), int(ov["to_index"])
        if end > shown_until or end >= n or start < 0:
            raise ValueError("overlay extends beyond the displayed bars")
        if int(ov["known_at"]) > shown_until:
            raise ValueError("overlay not yet knowable at the displayed time")
        style = state_style(str(ov.get("state", "CANDIDATE")))
        price = float(ov["price"])
        lo, hi = min(bars.low), max(bars.high)
        span = (hi - lo) or 1.0
        y = 8 + (hi - price) / span * (360 - 16)
        x1 = (start + 0.5) * (720 / n)
        x2 = (end + 0.5) * (720 / n)
        dash = f' stroke-dasharray="{style["dash"]}"' if style["dash"] else ""
        pieces.append(
            f'<line x1="{x1:.2f}" y1="{y:.2f}" x2="{x2:.2f}" y2="{y:.2f}" stroke="#1565c0" '
            f'opacity="{style["opacity"]}"{dash}/>'
            f'<text x="{x2:.2f}" y="{y - 4:.2f}" font-size="11">{escape(str(ov["name"]))} known {ov["known_at"]}; '
            f'earliest entry {ov.get("earliest_entry", "NOT RECORDED")}</text>'
        )
    return base.replace("</svg>", "".join(pieces) + "</svg>")


def card_html(entry: dict[str, Any], spec_sha256: str, *, last_validated: str) -> str:
    """Source-attributed strategy card with date, family, status and evidence tier (ALG-074, ALG-075)."""
    return (
        '<section class="card">'
        f"<h3>{escape(str(entry['title']))}</h3>"
        f"<p>Source: {escape(str(entry['author']))}, {escape(str(entry['platform']))}, accessed {escape(str(entry['access_date']))}</p>"
        f"<p>Family: {escape(str(entry['family']))} | Status: CODE IMPLEMENTATION, paper only | Evidence tier: DESCRIPTIVE</p>"
        f"<p>Last local validation: {escape(last_validated)} | Spec hash: {escape(spec_sha256[:16])}</p>"
        "<p>Performance figures from the source are SOURCE_CLAIM items, not results.</p>"
        "</section>"
    )


def radar_filter(rows: Sequence[dict[str, Any]], *, instrument: str | None = None, timeframe: str | None = None,
                 regime: str | None = None, max_age_days: int | None = None, min_maturity: int | None = None,
                 as_of: str | None = None) -> list[dict[str, Any]]:
    """Filter the radar; each kept row keeps its canonical event id unchanged (ALG-079)."""
    out: list[dict[str, Any]] = []
    for r in rows:
        if instrument and r.get("instrument") != instrument:
            continue
        if timeframe and r.get("timeframe") != timeframe:
            continue
        if regime and r.get("regime") != regime:
            continue
        if max_age_days is not None and as_of is not None:
            age = (datetime.fromisoformat(as_of) - datetime.fromisoformat(str(r["known_at"]))).days
            if age > max_age_days:
                continue
        if min_maturity is not None and int(r.get("maturity_bars", 0)) < min_maturity:
            continue
        out.append(dict(r))
    return out


def compare_panel(*, gross: float, net: float, benchmark_net: float, cash_net: float) -> dict[str, float]:
    """Gross, after-cost and incremental values kept as separate named fields (ALG-080)."""
    return {
        "gross_return": gross,
        "after_cost_return": net,
        "benchmark_after_cost": benchmark_net,
        "cash_return": cash_net,
        "incremental_vs_benchmark": net - benchmark_net,
        "incremental_vs_cash": net - cash_net,
    }


def why_not_log(log: list[dict[str, Any]], *, event_id: str, primary_constraint: str, at: str) -> list[dict[str, Any]]:
    """Abstentions are appended as records, never dropped (ALG-082). Returns the extended log."""
    if not primary_constraint:
        raise ValueError("a no-trade record needs a primary constraint")
    return [*log, {"event_id": event_id, "at": at, "decision": "NO_TRADE", "primary_constraint": primary_constraint}]


def demo_page(bars_by_symbol: dict[str, Bars], cards: Sequence[dict[str, Any]]) -> str:
    """One offline page: banner, cards and one chart per symbol (ALG-083). No scripts or external references."""
    parts = [
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>Algorithm research (paper only)</title>",
        "<style>body{font-family:system-ui,sans-serif;margin:16px;max-width:900px}"
        ".banner{background:#fff3cd;padding:8px;border:1px solid #c69500}.card{border:1px solid #ccc;padding:8px;margin:8px 0}"
        "@media (max-width:600px){body{margin:8px}}</style></head><body>",
        f'<div class="banner">{escape(PAPER_BANNER)}</div>',
    ]
    for c in cards:
        parts.append(f'<div class="card"><code>{escape(str(c["card_sha256"])[:16])}</code> {escape(str(c["family"]))}</div>')
    for symbol, bars in sorted(bars_by_symbol.items()):
        parts.append(f"<h2>{escape(symbol)}</h2>{render_candles_svg(bars, title=symbol)}")
    parts.append("</body></html>")
    page = "".join(parts)
    lowered = page.lower()
    for bad in FORBIDDEN_WORDS:
        if bad in lowered:
            raise ValueError(f"forbidden wording in page: {bad}")
    return page


__all__ = ["state_style", "money", "overlay_svg", "card_html", "radar_filter", "compare_panel", "why_not_log", "demo_page"]
