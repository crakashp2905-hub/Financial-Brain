"""Deterministic fixture pack (FE-0147): one complete session per outcome class. All values are synthetic."""
from __future__ import annotations

import copy

BASE = {
    "session_id": "FX-BASE",
    "decision_utc": "2027-01-04T03:46:00+00:00",
    "entry_earliest_utc": "2027-01-04T04:00:00+00:00",
    "side": "BUY", "instrument": "INE009A01021", "quantity": 10, "tick": "0.05",
    "strategy_sha256": "a" * 64, "snapshot_sha256": "b" * 64, "risk_sha256": "c" * 64, "benchmark_id": "FIXTURE_CASH",
    "quotes": [{"offset_ms": 300, "bid": "1500.00", "ask": "1500.05", "bid_qty": 100, "ask_qty": 100}],
    "latency_ms": 200, "max_wait_ms": 5000, "fee_bps": "3", "close_price": "1520.00",
    "feed": {"ok": True, "reasons": []},
}


def _make(name: str, **over) -> dict:
    fx = copy.deepcopy(BASE)
    fx["session_id"] = f"FX-{name}"
    fx.update(over)
    return fx


def fixture_pack() -> dict[str, dict]:
    return {
        "win": _make("win", close_price="1520.00"),
        "loss": _make("loss", close_price="1480.00"),
        "no_trade": _make("no_trade", feed={"ok": False, "reasons": ["STALE_QUOTE"]}),
        "rejection": _make("rejection", price_band=["1000", "1400"]),
        "late_quote": _make("late_quote", quotes=[{"offset_ms": 9000, "bid": "1500.00", "ask": "1500.05", "bid_qty": 100, "ask_qty": 100}]),
        "unknown_order": _make("unknown_order", ack_lost=True),
        "adverse_fill": _make("adverse_fill", passive=True, limit="1500.00", close_price="1500.50", adverse_bps="20",
                              trades=[{"offset_ms": 400, "price": "1499.50", "qty": 10}]),
        "missed_fill": _make("missed_fill", passive=True, limit="1500.00",
                             trades=[{"offset_ms": 400, "price": "1500.00", "qty": 10}]),
    }


EXPECTED = {"win": "WIN", "loss": "LOSS", "no_trade": "NO_TRADE", "rejection": "REJECTED", "late_quote": "LATE_QUOTE",
            "unknown_order": "UNKNOWN_ORDER", "adverse_fill": "LOSS", "missed_fill": "MISSED_FILL"}
