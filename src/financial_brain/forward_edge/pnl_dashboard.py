"""Ledger-gated after-cost strategy P&L and same-session benchmark view (FE-0118)."""
from __future__ import annotations

import html
import math
from dataclasses import dataclass
from pathlib import Path

from financial_brain.forward_edge.strategy_attribution import StrategyAttributionReport


class PnlDashboardError(ValueError):
    """P&L view cannot be built from inconsistent or unprovenanced inputs."""


def _number(row: dict[str, object], key: str) -> float:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PnlDashboardError(f"attribution field {key!r} is not a finite number")
    return float(value)


@dataclass(frozen=True)
class PnlDashboard:
    session: str
    pnl_evidence_class: str
    strategy_rows: tuple[dict[str, object], ...]
    portfolio_net_pnl: float
    benchmark_net_pnl: float
    benchmark_source_sha256: str
    attribution_sha256: str


def build_pnl_dashboard(attribution: StrategyAttributionReport, *, benchmark_net_pnl: float,
                        benchmark_source_sha256: str) -> PnlDashboard:
    """Require ledger reconciliation before exposing reported strategy P&L as a dashboard."""
    if not attribution.ledger_reconciled:
        raise PnlDashboardError("strategy attribution does not reconcile to ledger")
    if not math.isfinite(benchmark_net_pnl):
        raise PnlDashboardError("benchmark P&L must be finite")
    if len(benchmark_source_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in benchmark_source_sha256):
        raise PnlDashboardError("benchmark must carry a lowercase SHA-256 source identity")
    return PnlDashboard(attribution.session.isoformat(), attribution.pnl_evidence_class,
                        attribution.strategy_rows,
                        attribution.portfolio_net_pnl, benchmark_net_pnl,
                        benchmark_source_sha256, attribution.report_hash)


def render_pnl_dashboard(report: PnlDashboard, output: Path) -> Path:
    """Render component table and a compact P&L comparison chart, all values in caller currency."""
    max_abs = max([abs(report.benchmark_net_pnl), abs(report.portfolio_net_pnl),
                   *(abs(_number(row, "ledger_net_pnl")) for row in report.strategy_rows), 1.0])
    labels = [(str(row["strategy_id"]), _number(row, "ledger_net_pnl"))
              for row in report.strategy_rows]
    labels.extend((("Portfolio", report.portfolio_net_pnl), ("Benchmark", report.benchmark_net_pnl)))
    bars = []
    for label, value in labels:
        width = abs(value) / max_abs * 100
        color = "#27784b" if value >= 0 else "#b13830"
        bars.append("<div class='chart-row'><span>" + html.escape(label) + "</span><div class='track'>"
                    + f"<div class='bar' style='width:{width:.4f}%;background:{color}'></div></div><b>"
                    + f"{value:,.2f}</b></div>")
    table_rows = []
    for row in report.strategy_rows:
        gross = (_number(row, "realized_gross_pnl") + _number(row, "unrealized_pnl_change")
                 + _number(row, "cash_yield"))
        costs = sum(_number(row, key) for key in
                    ("slippage", "fees", "taxes", "financing", "other_charges"))
        values = (str(row["strategy_id"]), f"{gross:,.2f}", f"{_number(row, 'slippage'):,.2f}",
                  f"{_number(row, 'fees'):,.2f}", f"{_number(row, 'taxes'):,.2f}",
                  f"{_number(row, 'financing'):,.2f}", f"{_number(row, 'other_charges'):,.2f}",
                  f"{costs:,.2f}", f"{_number(row, 'calculated_net_pnl'):,.2f}",
                  f"{_number(row, 'ledger_net_pnl'):,.2f}",
                  f"{_number(row, 'ledger_difference'):,.4f}",
                  f"{_number(row, 'risk_contribution'):,.6f}")
        table_rows.append("<tr>" + "".join("<td>" + html.escape(value) + "</td>"
                                             for value in values) + "</tr>")
    table = "".join(table_rows) or "<tr><td colspan='12'>No strategy rows</td></tr>"
    page = ("<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            "<title>After-cost P&amp;L</title><style>body{font:14px system-ui;max-width:1200px;margin:2rem auto;padding:0 1rem;color:#202630}"
            "table{border-collapse:collapse;width:100%}td,th{padding:.45rem;border-bottom:1px solid #ddd;text-align:right}"
            "td:first-child,th:first-child{text-align:left}.chart-row{display:grid;grid-template-columns:140px 1fr 100px;gap:10px;align-items:center;margin:6px 0}"
            ".track{height:12px;background:#e9edf0}.bar{height:12px}.chart-row b{text-align:right}code{overflow-wrap:anywhere}</style><main>"
            f"<h1>After-cost P&amp;L — {html.escape(report.session)}</h1>"
            f"<p>Evidence class: <strong>{html.escape(report.pnl_evidence_class)}</strong></p>"
            "<p>Values are read from an attribution that reconciled to ledger within its configured tolerance. "
            "Filled orders are not assumed to be closed trades.</p><h2>Net P&amp;L comparison</h2>"
            + "".join(bars) + "<h2>Ledger reconciliation and cost components</h2><div style='overflow:auto'><table><thead><tr>"
            "<th>Strategy</th><th>Gross P&amp;L</th><th>Slippage</th><th>Fees</th><th>Taxes</th><th>Financing</th>"
            "<th>Other</th><th>Total costs</th><th>Calculated net</th><th>Ledger net</th><th>Difference</th>"
            "<th>Risk contribution</th></tr></thead><tbody>" + table + "</tbody></table></div>"
            "<h2>Evidence</h2><p>Attribution: <code>" + report.attribution_sha256 + "</code><br>Benchmark source: <code>"
            + report.benchmark_source_sha256 + "</code></p></main></html>")
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(page, encoding="utf-8")
    return destination
