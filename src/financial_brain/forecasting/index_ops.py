"""15-minute index program operations (checklist 24A/24C/24D/24E/24G).

* ``OptionableIndexMaster``: effective-dated set of index underlyings with listed options.
* ``decision_times``: one event per completed 15-minute interval inside the session.
* ``forecast_or_unavailable``: emits UNAVAILABLE instead of a forecast when inputs are stale/missing.
* ``ForecastLog``: append-only, hash-chained forecast events; duplicates refused.
* ``LogisticDirection``: a small versioned logistic model for P(UP) trained on completed bars only.
* ``option_card``: linked BUY CE/PE intention with exit terms and maximum planned loss.
* ``eod_audit``: end-of-day counts by index and state, with unresolved positions listed.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta


class OpsError(ValueError):
    pass


@dataclass
class OptionableIndexMaster:
    snapshots: list[tuple[date, frozenset[str], str]] = field(default_factory=list)

    def add(self, on: date, underlyings: set[str], source: str) -> None:
        self.snapshots.append((on, frozenset(underlyings), source))
        self.snapshots.sort(key=lambda s: s[0])

    def optionable(self, index: str, on: date) -> bool:
        known = [s for s in self.snapshots if s[0] <= on]
        if not known:
            raise OpsError("no optionable-index snapshot covers that date")
        return index in known[-1][1]


def decision_times(session_open: datetime, session_close: datetime,
                   step: timedelta = timedelta(minutes=15)) -> list[datetime]:
    out, t = [], session_open + step
    while t <= session_close:
        out.append(t)
        t += step
    return out


def forecast_or_unavailable(inputs_at: dict[str, datetime | None], now: datetime,
                            max_age: timedelta, required: set[str]) -> tuple[str, list[str]]:
    missing = [k for k in sorted(required) if inputs_at.get(k) is None]
    stale = [k for k in sorted(required) if inputs_at.get(k) is not None
             and now - inputs_at[k] > max_age]  # type: ignore[operator]
    if missing or stale:
        return "UNAVAILABLE", [f"missing {m}" for m in missing] + [f"stale {s}" for s in stale]
    return "FORECAST", []


@dataclass
class ForecastLog:
    events: list[dict] = field(default_factory=list)

    def append(self, event: dict) -> str:
        for k in ("forecast_id", "index_id", "bar_cutoff", "config_hash", "prediction"):
            if k not in event:
                raise OpsError(f"forecast event needs {k}")
        if any(e["forecast_id"] == event["forecast_id"] for e in self.events):
            raise OpsError("duplicate forecast id")
        prev = self.events[-1]["hash"] if self.events else ""
        body = json.dumps(event, sort_keys=True, default=str)
        h = hashlib.sha256((prev + body).encode()).hexdigest()
        self.events.append({**event, "prev": prev, "hash": h})
        return h

    def verify(self) -> bool:
        prev = ""
        for e in self.events:
            body = {k: v for k, v in e.items() if k not in ("prev", "hash")}
            h = hashlib.sha256((prev + json.dumps(body, sort_keys=True, default=str)).encode()).hexdigest()
            if e["prev"] != prev or e["hash"] != h:
                return False
            prev = h
        return True


@dataclass
class LogisticDirection:
    version: str
    weights: list[float] = field(default_factory=list)
    bias: float = 0.0

    def fit(self, x: list[list[float]], y: list[int], *, epochs: int = 300, lr: float = 0.1,
            l2: float = 0.01) -> LogisticDirection:
        if not x or len(x) != len(y):
            raise OpsError("need matching features and labels")
        n = len(x[0])
        self.weights, self.bias = [0.0] * n, 0.0
        for _ in range(epochs):
            gw, gb = [0.0] * n, 0.0
            for xi, yi in zip(x, y, strict=True):
                err = self.p_up(xi) - yi
                for j in range(n):
                    gw[j] += err * xi[j]
                gb += err
            m = len(x)
            self.weights = [w - lr * (g / m + l2 * w) for w, g in zip(self.weights, gw, strict=True)]
            self.bias -= lr * gb / m
        return self

    def p_up(self, xi: list[float]) -> float:
        z = self.bias + sum(w * v for w, v in zip(self.weights, xi, strict=True))
        return 1 / (1 + math.exp(-max(min(z, 50), -50)))


def option_card(*, forecast_id: str, token: str, kind: str, strike: float, expiry: date, lots: int,
                lot_size: int, entry_low: float, entry_high: float, sl_premium: float,
                tp_premium: float, underlying_invalidation: float, time_stop: datetime,
                quote_ttl: timedelta, expected_net: float) -> dict:
    if kind not in ("CE", "PE"):
        raise OpsError("kind must be CE or PE")
    if not (sl_premium < entry_low <= entry_high < tp_premium):
        raise OpsError("premium SL < entry band < TP required")
    return {
        "forecast_id": forecast_id, "action": f"BUY {kind}", "token": token, "strike": strike,
        "expiry": expiry.isoformat(), "lots": lots, "entry_band": [entry_low, entry_high],
        "exit": {"close_action": "SELL_TO_CLOSE", "sl_premium": sl_premium, "tp_premium": tp_premium,
                 "underlying_invalidation": underlying_invalidation,
                 "time_stop": time_stop.isoformat()},
        "quote_ttl_seconds": quote_ttl.total_seconds(),
        "max_planned_loss": (entry_high - sl_premium) * lots * lot_size,
        "expected_net": expected_net, "note": "a trigger is not a guaranteed fill",
    }


def eod_audit(records: list[dict]) -> dict:
    """``records`` carry index_id, state and optional pnl/cost. Unresolved states are listed."""
    by_index: dict[str, Counter] = {}
    pnl = cost = 0.0
    unresolved = []
    for r in records:
        by_index.setdefault(r["index_id"], Counter())[r["state"]] += 1
        pnl += r.get("pnl", 0.0)
        cost += r.get("cost", 0.0)
        if r["state"] in ("OPEN", "PARTIALLY_EXITED", "UNPRICED"):
            unresolved.append(r.get("forecast_id"))
    return {"by_index": {k: dict(v) for k, v in sorted(by_index.items())},
            "net_pnl": pnl - cost, "costs": cost, "unresolved": unresolved}
