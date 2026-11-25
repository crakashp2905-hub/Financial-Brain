"""Five-desk CLI targets over recorded JSON (FB-0489, FB-1009, FB-2433, FB-0311).

Read-only: every command reads a JSON file of recorded signals/exits/forecasts and prints a view.
Nothing here places an order or opens the database.

    python -m financial_brain.paper.five_desk_cli desks-status --file state.json
    python -m financial_brain.paper.five_desk_cli signals --file signals.json --with-exits
    python -m financial_brain.paper.five_desk_cli exits --file exits.json --desk all
    python -m financial_brain.paper.five_desk_cli trades-paired --file paired.json
    python -m financial_brain.paper.five_desk_cli pnl-by-exit-reason --file exits.json
    python -m financial_brain.paper.five_desk_cli heatmap --file forecasts.json
"""
from __future__ import annotations

import argparse
import csv
import io
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

DESKS = ("SWING", "INTRADAY", "OPTIONS", "ETF", "MUTUAL_FUND")


def _load(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data if isinstance(data, list) else [data]


def desks_status(rows: list[dict]) -> list[str]:
    by = {r["desk"]: r for r in rows}
    return [f"{d:12} {by.get(d, {}).get('status', 'NO_DATA')}" for d in DESKS]


def signals_view(rows: list[dict], with_exits: bool) -> list[str]:
    out = []
    for r in rows:
        line = f"{r['desk']:12} {r['instrument_id']:16} {r.get('side') or r['intent']:10}"
        if with_exits:
            line += f" SL {r.get('stop_price', '-')} TP {r.get('target_price', '-')} close {r.get('close_action', '-')}"
        out.append(line)
    return out


def exits_view(rows: list[dict], desk: str) -> list[str]:
    return [f"{r['desk']:12} {r['position_id']:10} {r['reason']:12} {r.get('fill_price', 'UNRESOLVED')}"
            for r in rows if desk == "all" or r["desk"] == desk.upper()]


def pnl_by_reason(rows: list[dict]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for r in rows:
        if r.get("pnl") is not None:
            out[r["reason"]] += float(r["pnl"])
    return dict(sorted(out.items()))


def heatmap(rows: list[dict]) -> list[str]:
    """Per-index direction with probability, model version and tradability (FB-2433)."""
    out = []
    for r in sorted(rows, key=lambda r: r["index_id"]):
        p = r["probs"]
        top = max(p, key=p.get)
        tag = "OPTIONS" if r.get("optionable") else "RESEARCH"
        out.append(f"{r['index_id']:12} {top:5} {p[top]:.2f} v{r['model_version']} {tag} @ {r['at']}")
    return out


def weekly_mf_review_due(today: date, last_review: date | None, every_days: int = 7) -> bool:
    """Periodic MF review scheduling (FB-1009)."""
    return last_review is None or (today - last_review).days >= every_days


def parse_portfolio_disclosure(text: str) -> tuple[list[dict], list[str]]:
    """Parse an AMC monthly portfolio CSV (name,isin,sector,weight). Returns rows and problems (FB-0311)."""
    rows: list[dict] = []
    problems: list[str] = []
    weights: list[float] = []
    for i, r in enumerate(csv.DictReader(io.StringIO(text)), start=2):
        try:
            w = float(r["weight"])
            rows.append({"name": r["name"], "isin": r["isin"], "sector": r.get("sector") or "UNKNOWN",
                         "weight": w})
            weights.append(w)
        except (KeyError, ValueError) as exc:
            problems.append(f"line {i}: {exc}")
    total = sum(weights)
    if rows and abs(total - 100) > 1:
        problems.append(f"weights sum to {total:.2f}, not 100")
    return rows, problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="five-desk")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("desks-status", "signals", "exits", "trades-paired", "pnl-by-exit-reason", "heatmap"):
        p = sub.add_parser(name)
        p.add_argument("--file", required=True)
        if name == "signals":
            p.add_argument("--with-exits", action="store_true")
        if name == "exits":
            p.add_argument("--desk", default="all")
    a = ap.parse_args(argv)
    rows = _load(a.file)
    if a.cmd == "desks-status":
        lines = desks_status(rows)
    elif a.cmd == "signals":
        lines = signals_view(rows, a.with_exits)
    elif a.cmd == "exits":
        lines = exits_view(rows, a.desk)
    elif a.cmd == "trades-paired":
        lines = [json.dumps(r, sort_keys=True) for r in rows]
    elif a.cmd == "pnl-by-exit-reason":
        lines = [f"{k:12} {v:,.2f}" for k, v in pnl_by_reason(rows).items()]
    else:
        lines = heatmap(rows)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
