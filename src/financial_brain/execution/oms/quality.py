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

import re
from datetime import datetime

import numpy as np

from . import model as M

ROUND_TRIP = {"mega": 0.0025, "large": 0.0030, "mid": 0.0045, "small": 0.0060}
MIN_ORDERS = 20
Bucket = tuple[str, str, str, str, str]


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


def slippage_bucket(*, spread_bp: float | None, adv_inr: float | None,
                    time_minute: int | None, participation: float | None,
                    volatility_pct: float | None) -> Bucket:
    """Return stable spread/ADV/time/participation/volatility bucket labels.

    Missing or invalid market context is explicit (`unknown`); it is never mapped to the cheapest bucket.
    `time_minute` is local exchange time in minutes after midnight, and volatility is a percent value.
    """
    def numeric(value: float | None) -> float | None:
        return float(value) if value is not None and np.isfinite(value) else None

    spread, adv, part, vol = map(numeric, (spread_bp, adv_inr, participation, volatility_pct))
    spread_key = ("unknown" if spread is None or spread < 0 else "lt_5bp" if spread < 5
                  else "5_15bp" if spread < 15 else "15_50bp" if spread < 50 else "gte_50bp")
    adv_key = bucket(adv)
    if time_minute is None or not isinstance(time_minute, int) or not 0 <= time_minute < 1440:
        time_key = "unknown"
    elif 555 <= time_minute < 600:
        time_key = "open_0915_1000"
    elif 600 <= time_minute < 870:
        time_key = "midday_1000_1430"
    elif 870 <= time_minute <= 930:
        time_key = "close_1430_1530"
    else:
        time_key = "outside_regular_session"
    part_key = ("unknown" if part is None or part < 0 else "le_1pct" if part <= 0.01
                else "1_5pct" if part <= 0.05 else "5_10pct" if part <= 0.10 else "gt_10pct")
    vol_key = ("unknown" if vol is None or vol < 0 else "lt_1pct" if vol < 1
               else "1_2pct" if vol < 2 else "2_4pct" if vol < 4 else "gte_4pct")
    return spread_key, adv_key, time_key, part_key, vol_key


def calibrate_slippage(observations: list[dict], *, min_observations: int = MIN_ORDERS) -> dict:
    """Build conservative five-dimensional slippage buckets from observed broker fills only.

    Each row must include context (`spread_bp`, `adv_inr`, `time_minute`, `participation`, `volatility_pct`),
    signed adverse `slippage_bp`, fidelity, an aware `observed_at` timestamp, and a 64-hex source hash. Simulated
    quote/bar rows are counted as excluded evidence. A bucket below the sample floor or without observed receipts
    returns no parameter and `UNCALIBRATED`; this report never mutates the active execution model.
    """
    if min_observations < 1:
        raise ValueError("min_observations must be positive")
    groups: dict[Bucket, list[float]] = {}
    excluded: dict[Bucket, int] = {}
    for row in observations:
        key = slippage_bucket(
            spread_bp=row.get("spread_bp"), adv_inr=row.get("adv_inr"),
            time_minute=row.get("time_minute"), participation=row.get("participation"),
            volatility_pct=row.get("volatility_pct"),
        )
        value = row.get("slippage_bp")
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            slip = float("nan")
        else:
            try:
                slip = float(value)
            except ValueError:
                slip = float("nan")
        stamp = row.get("observed_at")
        try:
            parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            has_aware_time = parsed.tzinfo is not None and parsed.utcoffset() is not None
        except (TypeError, ValueError):
            has_aware_time = False
        valid_source = isinstance(row.get("source_sha256"), str) and bool(
            re.fullmatch(r"[0-9a-f]{64}", row["source_sha256"]))
        observed = (row.get("fidelity") == "OBSERVED_BROKER" and np.isfinite(slip)
                    and has_aware_time and valid_source)
        if observed:
            groups.setdefault(key, []).append(slip)
        else:
            excluded[key] = excluded.get(key, 0) + 1

    result = {}
    for key in sorted(set(groups) | set(excluded)):
        values = groups.get(key, [])
        calibrated = len(values) >= min_observations
        result["|".join(key)] = {
            "spread": key[0], "adv": key[1], "time": key[2],
            "participation": key[3], "volatility": key[4],
            "observed_broker_fills": len(values), "excluded_unverified_rows": excluded.get(key, 0),
            "status": "CALIBRATED" if calibrated else "UNCALIBRATED",
            "recommended_slippage_bp": (max(0.0, float(np.quantile(values, 0.75, method="higher")))
                                         if calibrated else None),
        }
    return {"min_observations": min_observations, "buckets": result}


def register_calibrated_slippage(calibration: dict, *, registry, version: str) -> str:
    """Register a new immutable execution-model version only from complete observed calibration.

    Uncalibrated buckets are refused rather than defaulted to zero or to a simulated estimate.
    The registry is deliberately passed in, avoiding hidden active-model mutation.
    """
    buckets = calibration.get("buckets")
    if not isinstance(buckets, dict) or not buckets:
        raise ValueError("slippage calibration has no buckets")
    uncalibrated = [key for key, row in buckets.items() if row.get("status") != "CALIBRATED"
                    or row.get("recommended_slippage_bp") is None
                    or row.get("observed_broker_fills", 0) < calibration.get("min_observations", MIN_ORDERS)]
    if uncalibrated:
        raise ValueError("uncalibrated slippage buckets cannot change an execution model: "
                         + ", ".join(sorted(uncalibrated)))
    params = {"source": "OBSERVED_BROKER_CALIBRATION", "min_observations": calibration["min_observations"],
              "buckets": {key: {"recommended_slippage_bp": row["recommended_slippage_bp"],
                                "observed_broker_fills": row["observed_broker_fills"]}
                          for key, row in sorted(buckets.items())}}
    registry.register(version, params)
    return registry.sha256(version)
