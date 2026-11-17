"""Static HTML five-desk control room (FB-1101, FB-1102, FB-1103, FB-2301, FB-2305, FB-2433, FB-2434).

Renders recorded data only: consolidated NAV and P&L, five desk pages with their own clocks, the
prioritised Today list, the health screen, the index heatmap and option cards. Stale values are shown
as stale; nothing on the page can place an order. Output is a self-contained HTML string.
"""
from __future__ import annotations

from html import escape


def _row(cells: list[object]) -> str:
    return "<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in cells) + "</tr>"


def _table(head: list[str], rows: list[list[object]]) -> str:
    h = "".join(f"<th>{escape(x)}</th>" for x in head)
    return f"<table><thead><tr>{h}</tr></thead><tbody>{''.join(_row(r) for r in rows)}</tbody></table>"


def render(state: dict) -> str:
    c = state["consolidated"]
    parts = [
        "<h1>Five-desk paper control room</h1>",
        "<p class='note'>Paper only. No control on this page sends an order.</p>",
        "<h2>Consolidated</h2>",
        _table(["NAV", "Available", "Blocked", "Realized", "Unrealized", "Drawdown"],
               [[c.get("nav", "UNPRICED"), c["available"], c["blocked"], c["realized"],
                 c.get("unrealized", "STALE"), c["drawdown"]]]),
        "<h2>Today</h2>",
        _table(["Priority", "Kind", "Desk", "Time", "Text", "Rule", "Evidence", "SL", "TP"],
               [[a["level"], a["kind"], a["desk"], a["at"], a["text"], a["rule_version"],
                 a["evidence_tier"], a.get("sl", ""), a.get("tp", "")] for a in state["today"]]),
    ]
    for desk, page in state["desks"].items():
        parts.append(f"<h2>{escape(desk)} <small>{escape(page['clock'])}</small></h2>")
        parts.append(_table(["Signal", "Side", "Close action", "SL", "TP", "Trigger state", "Costs",
                             "Status"],
                            [[s["id"], s["side"], s["close_action"], s["sl"], s["tp"], s["trigger"],
                              s["costs"], s["status"]] for s in page.get("signals", [])]))
    h = state["health"]
    parts += ["<h2>Health</h2>",
              _table(["Status", "Problems", "Pending MF", "Expiring options"],
                     [[h["status"], ", ".join(h["problems"]) or "none", h["pending_mf"], h["expiring_options"]]]),
              "<h2>Index heatmap</h2>",
              _table(["Index", "Direction", "P", "Model", "Tradable", "As of"],
                     [[r["index_id"], r["top"], f"{r['p']:.2f}", r["model_version"],
                       "OPTIONS" if r["optionable"] else "RESEARCH", r["at"]] for r in state["heatmap"]]),
              "<h2>Option cards</h2>",
              _table(["Contract", "Action", "Entry band", "SL", "TP", "Max loss", "Net EV"],
                     [[o["token"], o["action"], o["entry_band"], o["exit"]["sl_premium"],
                       o["exit"]["tp_premium"], o["max_planned_loss"], o["expected_net"]]
                      for o in state["option_cards"]])]
    css = ("body{font-family:system-ui,sans-serif;margin:16px;background:#fff;color:#111}"
           "table{border-collapse:collapse;margin:8px 0}td,th{border:1px solid #ccc;padding:4px 8px}"
           ".note{color:#a00}@media(prefers-color-scheme:dark){body{background:#111;color:#eee}"
           "td,th{border-color:#444}}")
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>Five-desk control room</title>"
            f"<style>{css}</style></head><body>{''.join(parts)}</body></html>")
