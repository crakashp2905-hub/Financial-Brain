"""Execution quality by liquidity bucket: what the simulator assumed against what the broker did.

From the OMS journal alone (orders and their hash-chained events), per bucket (mega / large / mid / small by 20-day
rupee volume, as ``evaluation.edges``):

    orders, fill rate (filled / ordered quantity), partial share, broker rejections, risk rejections, unfilled
    cancels, NOT_FOUND
    slippage      signed bp of the average fill against the arrival price (positive = paid more than arrival)
    latency       seconds from SUBMITTED to the first PARTIAL/FILLED event
    modelled      half the bucket's modelled round trip, in bp - the simulator's assumption for one side
    gap           mean slippage minus modelled: positive means execution costs more than the backtests charged

Signal quality is kept apart from this (``paper/execution.attribution``: signal P&L at decision prices vs the fills),
so a strategy is never blamed for its fills, nor credited for them.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np

from . import model as M

ROUND_TRIP = {"mega": 0.0025, "large": 0.0030, "mid": 0.0045, "small": 0.0060}
MIN_ORDERS = 20


def bucket(adv_inr: float | None) -> str:
    if adv_inr is None or not np.isfinite(adv_inr):
        return "unknown"
    return "mega" if adv_inr >= 5e9 else "large" if adv_inr >= 1e9 else "mid" if adv_inr >= 2e8 else "small"


def _latency(events: list[dict]) -> dict[str, float]:
    sent: dict[str, datetime] = {}
    first: dict[str, datetime] = {}
    for e in events:
        at = datetime.fromisoformat(str(e["at"]))
        if e["state"] == M.SUBMITTED:
            sent.setdefault(e["client_id"], at)
        elif e["state"] in (M.PARTIAL, M.FILLED) and e["client_id"] in sent:
            first.setdefault(e["client_id"], at)
    return {c: (first[c] - sent[c]).total_seconds() for c in first}


def report(orders: list[M.Order], events: list[dict], adv: dict[str, float]) -> dict:
    lat = _latency(events)
    groups: dict[str, list[M.Order]] = {}
    for o in orders:
        groups.setdefault(bucket(adv.get(o.symbol)), []).append(o)
    out = {}
    for b, os_ in sorted(groups.items()):
        sent = [o for o in os_ if o.state != M.REJECTED_RISK]
        q = sum(o.qty for o in sent)
        filled = [o for o in sent if o.filled_qty and o.avg_price and o.arrival_price]
        slip = [(1 if o.side == M.BUY else -1) * (float(o.avg_price or 0) / float(o.arrival_price or 1) - 1) * 1e4
                for o in filled]
        modelled = ROUND_TRIP.get(b, np.nan) / 2 * 1e4
        ls = [lat[o.client_id] for o in sent if o.client_id in lat]
        mean_slip = float(np.mean(slip)) if slip else np.nan
        out[b] = {"orders": len(os_), "risk_rejected": sum(o.state == M.REJECTED_RISK for o in os_),
                  "broker_rejected": sum(o.state == M.REJECTED for o in sent),
                  "not_found": sum(o.state == M.NOT_FOUND for o in sent),
                  "fill_rate": sum(o.filled_qty for o in sent) / q if q else np.nan,
                  "partial_share": sum(0 < o.filled_qty < o.qty for o in sent) / len(sent) if sent else np.nan,
                  "slippage_bp": mean_slip, "modelled_bp": modelled,
                  "gap_bp": mean_slip - modelled if np.isfinite(mean_slip) else np.nan,
                  "latency_s_median": float(np.median(ls)) if ls else np.nan,
                  "judged": len(filled) >= MIN_ORDERS}
    return out
