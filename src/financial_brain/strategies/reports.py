"""Daily historical-paper report with explicit assumptions (ALG-120).

The report states every assumption next to its figures and says plainly that nothing here is independent
forward-market custody. It is generated from the inputs the caller provides and never reads a live source.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

ASSUMPTIONS = (
    "Fills follow the next-open rule; no fill is invented for a halted or zero-volume bar.",
    "Ambiguous intrabar paths take the adverse branch.",
    "Costs use the named cost-model version; no cost is omitted.",
    "All data is historical or synthetic. No forward market observation is claimed.",
    "Results are paper only and do not authorise any order.",
)
CUSTODY_NOTE = "Not independent forward-market custody: the record is generated locally and can be re-run by anyone."


def daily_paper_report(*, day: str, decisions: Sequence[dict[str, Any]], cost_model_version: str) -> dict[str, Any]:
    """Counts of trades, abstentions and realised paper P&L, with assumptions and the custody note attached."""
    trades = [d for d in decisions if d.get("decision") == "TRADE"]
    abstain = [d for d in decisions if d.get("decision") == "NO_TRADE"]
    realised = [float(d["realised"]) for d in trades if d.get("realised") is not None]
    no_fill = sum(1 for d in trades if d.get("realised") is None)
    realised_total = sum(realised)
    return {
        "day": day,
        "trades": len(trades),
        "abstentions": len(abstain),
        "trades_without_fill": no_fill,
        "realised_paper_pnl": realised_total,
        "cost_model_version": cost_model_version,
        "assumptions": list(ASSUMPTIONS),
        "custody": CUSTODY_NOTE,
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [f"Daily historical paper report, {report['day']}", ""]
    lines.append(f"Trades: {report['trades']}  Abstentions: {report['abstentions']}  Trades without fill: {report['trades_without_fill']}")
    lines.append(f"Realised paper P&L: {report['realised_paper_pnl']}")
    lines.append(f"Cost model: {report['cost_model_version']}")
    lines.append("")
    lines.append("Assumptions:")
    lines.extend(f"- {a}" for a in report["assumptions"])
    lines.append("")
    lines.append(report["custody"])
    return "\n".join(lines) + "\n"
