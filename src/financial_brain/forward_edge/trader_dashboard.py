"""Five-desk evidence-labeled paper trader dashboard snapshot and HTML renderer."""
from __future__ import annotations

import hashlib
import html
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class TraderDashboardError(ValueError):
    """Dashboard snapshots are incomplete, stale, or internally inconsistent."""


DESKS = frozenset({"SWING", "INTRADAY", "OPTIONS", "ETF", "MUTUAL_FUND"})
SOURCE_MODES = frozenset({"OBSERVED_MARKET_DATA", "FIXTURE", "HISTORICAL", "PAPER", "HYPOTHETICAL"})
_SHA = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class DashboardHolding:
    instrument: str
    quantity: float
    market_value_inr: float
    unrealized_pnl_inr: float


@dataclass(frozen=True)
class DeskDashboardSnapshot:
    desk: str
    as_of: datetime
    source_mode: str
    source_sha256: str
    newest_quote_age_seconds: float | None
    maximum_quote_age_seconds: float
    risk_policy_status: str
    holdings: tuple[DashboardHolding, ...]
    free_cash_inr: float
    daily_net_pnl_inr: float
    historical_net_pnl_inr: float

    def validate(self) -> None:
        if self.desk not in DESKS:
            raise TraderDashboardError("desk must match the five declared product-charter desks")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise TraderDashboardError("desk snapshot time must be timezone-aware")
        if self.source_mode not in SOURCE_MODES:
            raise TraderDashboardError("source mode must explicitly identify observed, fixture or modeled data")
        if not _SHA.fullmatch(self.source_sha256):
            raise TraderDashboardError("desk source needs a lowercase SHA-256")
        if self.risk_policy_status not in {"PASS", "BLOCK", "UNKNOWN"}:
            raise TraderDashboardError("risk policy state must be PASS, BLOCK or UNKNOWN")
        if (not math.isfinite(self.maximum_quote_age_seconds) or self.maximum_quote_age_seconds <= 0
                or self.newest_quote_age_seconds is not None
                and (not math.isfinite(self.newest_quote_age_seconds) or self.newest_quote_age_seconds < 0)):
            raise TraderDashboardError("quote ages must be finite and within a positive policy")
        numbers = (self.free_cash_inr, self.daily_net_pnl_inr, self.historical_net_pnl_inr,
                   *(value for holding in self.holdings for value in
                     (holding.quantity, holding.market_value_inr, holding.unrealized_pnl_inr)))
        if any(not math.isfinite(value) for value in numbers):
            raise TraderDashboardError("cash, P&L and holding values must be finite")
        if any(not holding.instrument.strip() for holding in self.holdings):
            raise TraderDashboardError("holding instrument identities are required")

    @property
    def quote_health(self) -> str:
        if self.newest_quote_age_seconds is None:
            return "UNKNOWN"
        return "FRESH" if self.newest_quote_age_seconds <= self.maximum_quote_age_seconds else "STALE"


def build_trader_dashboard(*, snapshots: tuple[DeskDashboardSnapshot, ...],
                           maximum_desk_skew_seconds: float = 60.0) -> dict[str, object]:
    """Validate the synchronized five-desk view and hash its snapshot contents."""
    if not math.isfinite(maximum_desk_skew_seconds) or maximum_desk_skew_seconds < 0:
        raise TraderDashboardError("maximum desk clock skew must be finite and non-negative")
    for snapshot in snapshots:
        snapshot.validate()
    desk_names = [snapshot.desk for snapshot in snapshots]
    if set(desk_names) != DESKS or len(desk_names) != len(DESKS):
        raise TraderDashboardError("dashboard requires exactly one snapshot for each of the five desks")
    newest = max(row.as_of.astimezone(timezone.utc) for row in snapshots)
    oldest = min(row.as_of.astimezone(timezone.utc) for row in snapshots)
    skew = (newest - oldest).total_seconds()
    rows = []
    for snapshot in sorted(snapshots, key=lambda item: item.desk):
        rows.append({
            "desk": snapshot.desk,
            "as_of": snapshot.as_of.astimezone(timezone.utc).isoformat(),
            "source_mode": snapshot.source_mode,
            "source_sha256": snapshot.source_sha256,
            "quote_age_seconds": snapshot.newest_quote_age_seconds,
            "quote_health": snapshot.quote_health,
            "risk_policy_status": snapshot.risk_policy_status,
            "holdings": [{"instrument": holding.instrument, "quantity": holding.quantity,
                          "market_value_inr": holding.market_value_inr,
                          "unrealized_pnl_inr": holding.unrealized_pnl_inr}
                         for holding in snapshot.holdings],
            "free_cash_inr": snapshot.free_cash_inr,
            "daily_net_pnl_inr": snapshot.daily_net_pnl_inr,
            "historical_net_pnl_inr": snapshot.historical_net_pnl_inr,
        })
    body: dict[str, object] = {
        "schema": "fb.trader_dashboard_snapshot.v1",
        "desk_count": len(rows),
        "synchronized": skew <= maximum_desk_skew_seconds,
        "maximum_desk_skew_seconds": maximum_desk_skew_seconds,
        "observed_desk_skew_seconds": skew,
        "global_risk_status": ("BLOCK" if skew > maximum_desk_skew_seconds
                               or any(row["risk_policy_status"] != "PASS" for row in rows)
                               or any(row["quote_health"] != "FRESH" for row in rows) else "PASS"),
        "desks": rows,
        "physical_orders_enabled": False,
    }
    body["snapshot_sha256"] = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()
    return body


def render_trader_dashboard(report: dict[str, object], output: Path) -> Path:
    """Render an escaped five-desk overview with visible source and freshness labels."""
    desks = report.get("desks")
    if report.get("schema") != "fb.trader_dashboard_snapshot.v1" or not isinstance(desks, list):
        raise TraderDashboardError("a validated five-desk dashboard snapshot is required")
    cards = []
    for row in desks:
        assert isinstance(row, dict)
        raw_holdings = row.get("holdings")
        if not isinstance(raw_holdings, list) or any(not isinstance(item, dict) for item in raw_holdings):
            raise TraderDashboardError("desk holdings must be a list of validated holding records")
        holdings = "".join("<li>" + html.escape(str(item["instrument"])) + ": "
                           + html.escape(str(item["quantity"])) + " units; value INR "
                           + f"{float(item['market_value_inr']):,.2f}; unrealized INR "
                           + f"{float(item['unrealized_pnl_inr']):,.2f}</li>"
                           for item in raw_holdings)
        cards.append("<section><h2>" + html.escape(str(row["desk"])) + "</h2><p>Source: <b>"
                     + html.escape(str(row["source_mode"])) + "</b> · quote: <b>"
                     + html.escape(str(row["quote_health"])) + "</b> · risk: <b>"
                     + html.escape(str(row["risk_policy_status"])) + "</b></p><p>Quote age seconds: "
                     + html.escape(str(row["quote_age_seconds"])) + " · free cash INR "
                     + f"{float(row['free_cash_inr']):,.2f}</p><p>Daily net P&amp;L INR "
                     + f"{float(row['daily_net_pnl_inr']):,.2f}; historical net P&amp;L INR "
                     + f"{float(row['historical_net_pnl_inr']):,.2f}</p><ul>"
                     + (holdings or "<li>No holdings</li>") + "</ul><code>"
                     + html.escape(str(row["source_sha256"])) + "</code></section>")
    page = ("<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            "<title>Financial Brain trader dashboard</title><style>body{font:15px system-ui;max-width:1300px;margin:2rem auto;padding:0 1rem;background:#f5f7fa;color:#18212b}"
            "main{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:1rem}section{background:#fff;padding:1rem;border:1px solid #d8dee6;border-radius:10px}"
            "code{font-size:11px;overflow-wrap:anywhere}h1{margin-bottom:.2rem}.banner{font-weight:bold;padding:.8rem;background:#f2e7c8}</style><h1>Five-desk paper trader dashboard</h1>"
            f"<p class='banner'>Global risk status: {html.escape(str(report['global_risk_status']))} · physical orders disabled</p><main>"
            + "".join(cards) + "</main><p>Snapshot SHA-256: <code>"
            + html.escape(str(report["snapshot_sha256"])) + "</code></p></html>")
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page, encoding="utf-8")
    return target
