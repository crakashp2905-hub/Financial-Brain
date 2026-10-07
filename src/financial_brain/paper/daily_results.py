"""The day's results, in one report: what the paper engine traded from the open to the close and how it did.

Read from the paper ledger (and the books and your portfolio when they exist), written to
``data/paper_reports/DATE-results.md`` and returned as text. Live trades are the evidence; if the live run did not
complete, the report says so and shows any replay of the day separately, labelled as one.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

REPORTS = Path("data") / "paper_reports"


def build(ledger, day: str, *, book=None) -> str:
    from . import execution as XA
    from . import loss_review as LR
    lines = [f"# Financial-Brain paper results - {day}", "", "Paper only: no order exists in this system. Information, not advice.", ""]
    run = ledger.execute("SELECT mode, status, polls, started, finished, note FROM runs WHERE day = ? ORDER BY mode", [day]).fetchall()
    if run:
        for mode, status, polls, st, fin, note in run:
            lines.append(f"- **{mode} run:** {status}, {polls or 0} polls, {str(st)[11:16]}-{str(fin)[11:16] if fin else '?'}")
    else:
        lines.append("- **No run recorded today** (a holiday, or the run did not start - check the Kite login).")
    t = ledger.execute("""SELECT mode, strategy, symbol, side, qty, entry_px, exit_px, exit_reason, gross_pnl, costs, net_pnl,
                                 entry_px * qty AS notional, mkt_ret FROM trades WHERE day = ? AND exit_ts IS NOT NULL""", [day]).df()
    if t.empty:
        lines += ["", "No closed paper trades today."]
    for mode in ("live", "replay"):
        x = t[t["mode"] == mode]
        if x.empty:
            continue
        label = "LIVE (the evidence)" if mode == "live" else "REPLAY (rebuilt from minute bars after the day - not evidence)"
        lines += ["", f"## {label}", "",
                  f"**{len(x)} trades · net Rs {x['net_pnl'].sum():,.0f}** (gross Rs {x['gross_pnl'].sum():,.0f}, charges Rs {x['costs'].sum():,.0f}) · "
                  f"{(x['net_pnl'] > 0).mean():.0%} won · {x['net_pnl'].sum() / x['notional'].sum() * 1e4:+.1f} bp of the money traded", "",
                  "| strategy | trades | won | gross Rs | charges Rs | net Rs | net bp/trade |", "|---|---|---|---|---|---|---|"]
        for s, g in x.groupby("strategy"):
            lines.append(f"| {s} | {len(g)} | {(g['net_pnl'] > 0).sum()} | {g['gross_pnl'].sum():,.0f} | {g['costs'].sum():,.0f} | "
                         f"{g['net_pnl'].sum():,.0f} | {(g['net_pnl'] / g['notional']).mean() * 1e4:+.1f} |")
        sides = x.groupby("side")["net_pnl"].agg(["count", "sum"])
        lines.append("")
        lines.append("By side: " + "; ".join(f"{k} {int(r['count'])} trades, Rs {r['sum']:,.0f}" for k, r in sides.iterrows()))
        for title, rows in (("Best", x.nlargest(5, "net_pnl")), ("Worst", x.nsmallest(5, "net_pnl"))):
            lines.append(f"{title}: " + ", ".join(f"{r.symbol} {r.strategy} {r.side} Rs {r.net_pnl:,.0f} ({r.exit_reason})" for r in rows.itertuples()))
        xa = [a for a in XA.attribution(ledger, mode=mode)]
        if xa:
            lines += ["", "Where the money went (all days in this mode so far): " + "; ".join(
                f"{a['strategy']}: signal Rs {a['signal_inr']:,.0f}, execution Rs {a['execution_inr']:,.0f}, charges Rs {a['charges_inr']:,.0f}"
                for a in xa)]
        aligned = x.dropna(subset=["mkt_ret"])
        if len(aligned):
            up = aligned["side"] == "long"
            w = aligned[(aligned["mkt_ret"] > 0) == up]
            a = aligned[(aligned["mkt_ret"] > 0) != up]
            lines.append(f"Your rule (only with the market), on today's trades: with the market {len(w)} trades Rs {w['net_pnl'].sum():,.0f}; "
                         f"against it {len(a)} trades Rs {a['net_pnl'].sum():,.0f} (a tracked view; the rule failed its held-out test).")
    ev = [e for e in __import__("financial_brain.paper.intraday_runner", fromlist=["evidence"]).evidence(ledger, "live")]
    if ev:
        lines += ["", "## Live record so far", ""]
        for e in ev:
            lines.append(f"- {e['strategy']}: {e['trades']} trades over {e['days']} day(s), net Rs {e['net_inr']:,.0f}, "
                         f"{e['net_bp_per_trade']:+.1f} bp a trade - {e['status']} (backtest expected {e['backtest_bp']} bp)")
    if book is not None:
        from . import book as BK
        lines += ["", "## Paper books (bought and sold at closes; updated by the 18:45 refresh)", ""]
        for spec in BK.BOOKS.values():
            s = BK.summary(book, "live", spec)
            extra = f", open P&L Rs {s['open_net_rs']:,.0f} (universe Rs {s['open_bench_rs']:,.0f})" if "open_net_rs" in s else ""
            lines.append(f"- {spec.label}: {s['closed']} sold, {s['open']} open{extra} - {s['verdict']}")
    try:
        import duckdb as _db
        from . import zoo as Z
        if Z.PATH.exists():
            zc = _db.connect(str(Z.PATH), read_only=True)
            try:
                sm = Z.summary(zc)
            finally:
                zc.close()
            if len(sm):
                lines += ["", "## The zoo - every tested daily strategy on paper (rules frozen as tested; no verdict before 60 sessions)", "",
                          "| strategy | kind | sessions | net since start | vs its benchmark | holdings |", "|---|---|---|---|---|---|"]
                for name, r in sm.iterrows():
                    lines.append(f"| {name} | {r['kind']} | {int(r['days'])} | {r['net_total'] * 100:+.2f}% | {r['vs_bench'] * 100:+.2f}% | {int(r['holdings'])} |")
    except Exception as e:                              # noqa: BLE001 - the report stands without it
        lines.append(f"(zoo unavailable: {str(e)[:80]})")
    return "\n".join(lines) + "\n"


def write(text: str, day: str) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    p = REPORTS / f"{day}-results.md"
    p.write_text(text, encoding="utf-8")
    return p
