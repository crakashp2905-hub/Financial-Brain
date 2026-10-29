"""Read-only, same-day live/replay paper-trade reconciliation.

Different days are never compared as if they were paired. A match requires the exact
day, strategy, symbol and side, with one closed trade in each mode. Differences are
diagnostics about feed, trigger, sizing or execution assumptions, not an edge verdict.
"""
from __future__ import annotations

from collections import Counter
from datetime import date


def compare(con, *, start: date | None = None, end: date | None = None) -> dict:
    rows = con.execute("""
        SELECT day, mode, strategy, symbol, side, qty, entry_ts, exit_ts,
               entry_px, exit_px, gross_pnl, costs, net_pnl
        FROM trades WHERE exit_ts IS NOT NULL AND mode IN ('live', 'replay')
          AND (? IS NULL OR CAST(day AS DATE) >= ?)
          AND (? IS NULL OR CAST(day AS DATE) <= ?)
        ORDER BY day, strategy, symbol, side, mode
    """, [start, start, end, end]).fetchall()
    names = ("day", "mode", "strategy", "symbol", "side", "qty", "entry_ts", "exit_ts",
             "entry_px", "exit_px", "gross_pnl", "costs", "net_pnl")
    records = [dict(zip(names, row, strict=True)) for row in rows]
    grouped: dict[tuple, dict[str, list[dict]]] = {}
    for row in records:
        key = (str(row["day"]), row["strategy"], row["symbol"], row["side"])
        grouped.setdefault(key, {}).setdefault(row["mode"], []).append(row)
    counts = Counter()
    pairs = []
    by_day = {}
    for key, modes in grouped.items():
        live, replay = modes.get("live", []), modes.get("replay", [])
        day = key[0]
        d = by_day.setdefault(day, Counter())
        if len(live) > 1 or len(replay) > 1:
            counts["ambiguous_keys"] += 1
            d["ambiguous_keys"] += 1
            continue
        if not live or not replay:
            label = "live_only" if live else "replay_only"
            counts[label] += 1
            d[label] += 1
            continue
        a, b = live[0], replay[0]
        an, bn = float(a["entry_px"]) * int(a["qty"]), float(b["entry_px"]) * int(b["qty"])
        if min(an, bn) <= 0:
            counts["invalid_notional"] += 1
            d["invalid_notional"] += 1
            continue
        counts["paired"] += 1
        d["paired"] += 1
        pairs.append({"day": day, "strategy": key[1], "symbol": key[2], "side": key[3],
                      "live_net_bp": float(a["net_pnl"]) / an * 1e4,
                      "replay_net_bp": float(b["net_pnl"]) / bn * 1e4,
                      "live_cost_bp": float(a["costs"]) / an * 1e4,
                      "replay_cost_bp": float(b["costs"]) / bn * 1e4,
                      "live_entry": str(a["entry_ts"]), "replay_entry": str(b["entry_ts"])})
    for pair in pairs:
        pair["net_gap_bp"] = pair["live_net_bp"] - pair["replay_net_bp"]
    live_days = sorted({str(r["day"]) for r in records if r["mode"] == "live"})
    replay_days = sorted({str(r["day"]) for r in records if r["mode"] == "replay"})
    overlap = sorted(set(live_days) & set(replay_days))
    return {"status": "PAIRED_DIAGNOSTIC" if pairs else "NO_PAIRED_TRADES",
            "live_days": len(live_days), "replay_days": len(replay_days),
            "same_days": overlap, "counts": dict(counts),
            "mean_net_gap_bp": sum(p["net_gap_bp"] for p in pairs) / len(pairs) if pairs else None,
            "by_day": {day: dict(c) for day, c in sorted(by_day.items())},
            "pairs": pairs,
            "note": "Only exact same-day, same-strategy, same-symbol, same-side closed paper trades are paired. "
                    "Unmatched trades and different days are not an execution-cost comparison; "
                    "neither mode is live brokerage P&L."}
