"""Four-Node predicates, entry, stops, trailing, exits, Indian conversion and funnel (FB-2515 to FB-2542).

Every predicate returns a reason code. If the variant is not executable (unresolved ambiguities) the
setup is BLOCKED. Entry is never filled at the breakout bar's close: the order is actionable on the
next bar. Distances are in "units" — pips for the XAU reference, ATR multiples for India.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal, InvalidOperation
import hashlib
import json
from math import isfinite

from financial_brain.strategies.four_node.indicators import Cluster20
from financial_brain.strategies.four_node.nodes import Node
from financial_brain.strategies.four_node.spec import Variant


@dataclass(frozen=True)
class Setup:
    status: str                                  # ELIGIBLE, REJECTED, BLOCKED
    side: str | None
    passed: tuple[str, ...]
    failed: tuple[str, ...]
    entry_bar: int | None = None
    stop: float | None = None
    target: float | None = None


def _direction(n1: Node, n2: Node, n3: Node) -> str | None:
    if (n1.side, n2.side, n3.side) == (1, -1, 1):
        return "SELL"          # H-L-H: break below N2
    if (n1.side, n2.side, n3.side) == (-1, 1, -1):
        return "BUY"           # L-H-L: break above N2
    return None


def evaluate(v: Variant, nodes: tuple[Node, Node, Node], c20: Cluster20, bar: int, close: float,
             barriers: list[float], unit: float, spread: float, *,
             d1_inclusive: bool = True, d2_inclusive: bool = False,
             tick: float | None = None) -> Setup:
    """Evaluate a completed bar ``bar`` closing at ``close`` against N1..N3 (all observable)."""
    if not v.executable() and v.executable_intent:
        return Setup("BLOCKED", None, (), tuple(f"unresolved:{u}" for u in v.unresolved()))
    n1, n2, n3 = nodes
    if any(n.observable_at is None or n.observable_at >= bar for n in nodes):
        return Setup("REJECTED", None, (), ("node_not_observable_before_breakout_bar",))
    side = _direction(n1, n2, n3)
    if side is None:
        return Setup("REJECTED", None, (), ("not_zigzag",))
    if not isfinite(unit) or unit <= 0:
        return Setup("BLOCKED", side, (), ("invalid_distance_unit",))
    if not isfinite(spread) or spread < 0:
        return Setup("BLOCKED", side, (), ("invalid_spread",))
    p = v.params
    ok: list[str] = []
    bad: list[str] = []

    def check(name: str, cond: bool) -> None:
        (ok if cond else bad).append(name)

    sell = side == "SELL"
    check("breakout_close", close < n2.price if sell else close > n2.price)
    if sell:
        check("n1_vs_20c_high", n1.price >= c20.high)
        check("n3_vs_20c_mid", n3.price <= c20.mid)
        check("n2_vs_20c_low", n2.price <= c20.low)
    else:
        check("n1_vs_20c_low", n1.price <= c20.low)
        mid_rule = v.choices.get("BUY_N3_MID", "LITERAL_LE_MID")
        check("n3_vs_20c_mid", n3.price <= c20.mid if mid_rule == "LITERAL_LE_MID" else n3.price >= c20.mid)
        n2_rule = v.choices.get("N2_BUY_COMPARATOR", "LITERAL_LE_HIGH")
        check("n2_vs_20c_high", n2.price <= c20.high if n2_rule == "LITERAL_LE_HIGH" else n2.price >= c20.high)
    k = "" if v.market == "XAUUSD" else "_atr"
    d1 = abs(n2.price - n3.price) / unit
    d2 = abs(close - n2.price) / unit
    d1_limit, d2_limit = p[f"max_d1{k}"], p[f"max_d2{k}"]
    check("d1_max", d1 <= d1_limit if d1_inclusive else d1 < d1_limit)
    check("d2_max", d2 <= d2_limit if d2_inclusive else d2 < d2_limit)
    stop = n3.price + spread if sell else n3.price - spread
    if tick is not None:
        if not isfinite(tick) or tick <= 0:
            return Setup("BLOCKED", side, tuple(ok), tuple(bad + ["invalid_tick_size"]))
        stop = round_stop(stop, tick, side)
    check("stop_side_valid", stop > close if sell else stop < close)
    risk = abs(close - stop) / unit
    check("max_sl", risk <= p[f"max_sl{k}"])
    ahead, _behind = barriers_split(close, side, barriers)
    check("barrier_clearance", all(
        abs(close - b) / unit >= p[f"min_struct{k}"] for b in ahead
    ))
    tp = p[f"tp{k}"] * unit
    target = close - tp if sell else close + tp
    status = "ELIGIBLE" if not bad else "REJECTED"
    return Setup(status, side, tuple(ok), tuple(bad), bar + 1 if not bad else None, stop, target)


@dataclass
class Trail:
    """Original rule: at +start units remove TP and lock +step; ratchet every further +step."""

    side: str
    entry: float
    stop: float
    unit: float
    start: float
    step: float
    tp_active: bool = True
    revisions: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if (not all(isfinite(value) for value in (self.entry, self.stop, self.unit, self.start, self.step))
                or self.entry <= 0 or self.stop <= 0 or self.unit <= 0 or self.start < 0
                or self.step <= 0):
            raise ValueError("trail prices/units must be finite and positive; start nonnegative")

    def on_price(self, executable_price: float) -> float:
        if not isfinite(executable_price) or executable_price <= 0:
            raise ValueError("executable price must be finite and positive")
        sign = 1 if self.side == "BUY" else -1
        gain = (executable_price - self.entry) * sign / self.unit
        if gain >= self.start:
            self.tp_active = False
            locked = self.step * (1 + int((gain - self.start) // self.step))
            new = self.entry + sign * locked * self.unit
            if (new - self.stop) * sign > 0:
                self.stop = new
                self.revisions.append(new)
        return self.stop

    def on_quote(self, bid: float, ask: float) -> float:
        """Update using bid for a long exit and ask for a short exit."""
        return self.on_price(trail_price(self.side, bid, ask))


def round_stop(stop: float, tick: float, side: str) -> float:
    """Round a stop to tick *away* from entry so rounding never tightens risk (FB-2520)."""
    import math
    if side not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL")
    if not (isfinite(stop) and isfinite(tick)) or tick <= 0:
        raise ValueError("stop must be finite and tick must be positive")
    n = stop / tick
    return (math.ceil(n - 1e-9) if side == "SELL" else math.floor(n + 1e-9)) * tick


def trail_price(side: str, bid: float, ask: float) -> float:
    """Trail on the executable exit side: a long exits at the bid, a short at the ask (FB-2523)."""
    if side not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL")
    if not (isfinite(bid) and isfinite(ask)) or bid <= 0 or ask <= 0:
        raise ValueError("bid/ask must be finite and positive")
    if ask < bid:
        raise ValueError("crossed quote")
    return bid if side == "BUY" else ask


def barriers_split(entry: float, side: str, barriers: list[float]) -> tuple[list[float], list[float]]:
    """Barriers ahead of the trade (in the profit direction) vs behind it (FB-2519)."""
    ahead = [b for b in barriers if (b > entry if side == "BUY" else b < entry)]
    behind = [b for b in barriers if b not in ahead]
    return ahead, behind


def nodes_expired(nodes: tuple[Node, Node, Node], bar: int, max_age_bars: int) -> bool:
    """A setup is invalid once N1 is older than ``max_age_bars`` (versioned parameter, FB-2514)."""
    return bar - nodes[0].pivot_at > max_age_bars


def significant_candle(o: float, h: float, low: float, c: float, rule: str | None) -> bool | None:
    """None when the definition is unresolved: callers must treat None as BLOCKED (FB-2524)."""
    body = abs(c - o)
    upper, lower = h - max(o, c), min(o, c) - low
    if rule == "BOTH_WICKS_GT_BODY":
        return upper > body and lower > body
    if rule == "COMBINED_WICK_GT_BODY":
        return upper + lower > body
    return None


@dataclass(frozen=True)
class InstrumentConfig:
    """Indian conversion (FB-2525): units, tick, multiplier and costs per instrument."""

    symbol: str
    tick: float
    multiplier: int
    atr: float
    spread: float
    slippage: float
    trading_hours: tuple[str, str] | None = None
    corporate_action_factor: float = 1.0

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("instrument symbol is required")
        if type(self.multiplier) is not int or self.multiplier <= 0:
            raise ValueError("contract multiplier must be a positive integer")
        if (not all(isfinite(value) for value in (self.tick, self.atr, self.spread,
                                                  self.slippage, self.corporate_action_factor))
                or self.tick <= 0 or self.atr <= 0 or self.spread < 0 or self.slippage < 0
                or self.corporate_action_factor <= 0):
            raise ValueError("instrument units/costs/action factor are invalid")
        if self.trading_hours is not None:
            if len(self.trading_hours) != 2:
                raise ValueError("trading_hours must contain open and close times")
            opened, closed = (time.fromisoformat(value) for value in self.trading_hours)
            if opened >= closed:
                raise ValueError("instrument trading hours must open before close")


def size_by_risk(capital_at_risk: float, entry: float, stop: float, cfg: InstrumentConfig,
                 lot: int = 1) -> int:
    """Units from rupee risk budget, rounded down to whole lots (FB-2526). Never the forex X rule."""
    if (not all(isfinite(value) for value in (capital_at_risk, entry, stop))
            or capital_at_risk < 0 or entry <= 0 or stop <= 0):
        raise ValueError("risk budget and prices must be finite, with nonnegative budget and positive prices")
    if type(lot) is not int or lot <= 0:
        raise ValueError("lot must be a positive integer")
    per_unit = abs(entry - stop) * cfg.multiplier + cfg.spread + cfg.slippage
    if per_unit <= 0:
        raise ValueError("zero risk per unit")
    units = int(capital_at_risk / per_unit)
    while units > 0 and units * per_unit > capital_at_risk:
        units -= 1
    return units - units % lot


FUNNEL_STAGES = ("pivots", "patterns", "setups", "intents", "fills", "closes")
_FUNNEL_OUTCOMES = {
    "pivots": {"OBSERVED"},
    "patterns": {"CONFIRMED", "REJECTED", "EXPIRED"},
    "setups": {"QUALIFIED", "REJECTED", "EXPIRED"},
    "intents": {"BUY", "SELL", "REJECTED", "EXPIRED"},
    "fills": {"FILLED", "PARTIAL", "NO_FILL", "REJECTED", "EXPIRED"},
    "closes": {"CLOSED", "STOP", "TARGET", "TIME_EXIT"},
}


@dataclass(frozen=True)
class FunnelEvent:
    stage: str
    event_id: str
    parent_event_ids: tuple[str, ...]
    outcome: str
    occurred_at: datetime | None = None
    gross_pnl_inr: Decimal | None = None
    costs_inr: Decimal | None = None

    @property
    def net_pnl_inr(self) -> Decimal | None:
        if self.gross_pnl_inr is None or self.costs_inr is None:
            return None
        return self.gross_pnl_inr - self.costs_inr


@dataclass
class Funnel:
    """Append-only, event-ID-reconciled Four-Node funnel (FB-2541)."""

    counts: Counter = field(default_factory=Counter)
    ids: dict[str, set[str]] = field(default_factory=dict)
    events: dict[str, FunnelEvent] = field(default_factory=dict)

    def add(self, stage: str, event_id: str, *, parents: tuple[str, ...] = (),
            outcome: str | None = None, occurred_at: datetime | None = None,
            gross_pnl_inr: Decimal | str | int | float | None = None,
            costs_inr: Decimal | str | int | float | None = None) -> None:
        """Record one stage event and its immediate parent IDs; repeats are idempotent."""
        if stage not in _FUNNEL_OUTCOMES:
            raise ValueError("unknown Four-Node funnel stage")
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("funnel event ID is required")
        if len(parents) != len(set(parents)):
            raise ValueError("funnel parent IDs must be unique")
        normalized_outcome = (outcome or ("OBSERVED" if stage == "pivots" else "")).upper()
        if normalized_outcome not in _FUNNEL_OUTCOMES[stage]:
            raise ValueError("outcome is invalid for funnel stage")
        if occurred_at is not None and (occurred_at.tzinfo is None or occurred_at.utcoffset() is None):
            raise ValueError("funnel event timestamp must be timezone-aware")

        def amount(value: Decimal | str | int | float | None, name: str) -> Decimal | None:
            if value is None:
                return None
            try:
                parsed = Decimal(str(value))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"{name} must be a finite decimal") from exc
            if not parsed.is_finite():
                raise ValueError(f"{name} must be a finite decimal")
            return parsed

        gross = amount(gross_pnl_inr, "gross P&L")
        costs = amount(costs_inr, "costs")
        if stage == "closes":
            if gross is None or costs is None or costs < 0:
                raise ValueError("close events require gross P&L and nonnegative costs")
        elif gross is not None or costs is not None:
            raise ValueError("P&L is recorded only on close events")

        index = FUNNEL_STAGES.index(stage)
        required_parent_stage = None if index == 0 else FUNNEL_STAGES[index - 1]
        if index == 0 and parents:
            raise ValueError("raw pivots cannot have funnel parents")
        if index > 0 and not parents:
            raise ValueError("funnel stage requires parent event IDs")
        for parent_id in parents:
            parent = self.events.get(parent_id)
            if parent is None or parent.stage != required_parent_stage:
                raise ValueError("funnel parent is missing or belongs to the wrong stage")
            if occurred_at is not None and parent.occurred_at is not None and parent.occurred_at > occurred_at:
                raise ValueError("funnel event cannot precede its parent")
        if stage == "patterns" and len(parents) < 3:
            raise ValueError("confirmed pattern requires at least three pivot events")
        if stage == "setups" and any(self.events[p].outcome != "CONFIRMED" for p in parents):
            raise ValueError("setup parent pattern must be confirmed")
        if stage == "intents" and normalized_outcome in {"BUY", "SELL"} and any(
                self.events[p].outcome != "QUALIFIED" for p in parents):
            raise ValueError("trade intent requires a qualified setup")
        if stage == "fills" and normalized_outcome in {"FILLED", "PARTIAL"} and any(
                self.events[p].outcome not in {"BUY", "SELL"} for p in parents):
            raise ValueError("paper fill requires a directional intent")
        if stage == "closes" and any(self.events[p].outcome not in {"FILLED", "PARTIAL"} for p in parents):
            raise ValueError("close requires one or more observed paper fills")

        event = FunnelEvent(stage, event_id, parents, normalized_outcome, occurred_at, gross, costs)
        prior = self.events.get(event_id)
        if prior is not None:
            if prior != event:
                raise ValueError("funnel event ID reused with conflicting payload")
            return
        self.events[event_id] = event
        self.ids.setdefault(stage, set()).add(event_id)
        self.counts[stage] += 1

    def reconciles(self, order: list[str]) -> bool:
        """Verify unique IDs, complete stage ancestry, and exact per-stage count reconciliation."""
        if tuple(order) != FUNNEL_STAGES:
            return False
        if len(self.events) != sum(self.counts.values()):
            return False
        for stage in order:
            event_ids = self.ids.get(stage, set())
            if self.counts[stage] != len(event_ids):
                return False
            if any(event_id not in self.events or self.events[event_id].stage != stage for event_id in event_ids):
                return False
        for event in self.events.values():
            index = order.index(event.stage)
            if index and (not event.parent_event_ids or any(
                    self.events[parent_id].stage != order[index - 1] for parent_id in event.parent_event_ids)):
                return False
            if event.stage == "closes":
                if event.gross_pnl_inr is None or event.costs_inr is None:
                    return False
                if event.net_pnl_inr != event.gross_pnl_inr - event.costs_inr:
                    return False
        return True

    def report(self) -> dict[str, object]:
        """Return counts and exact close-event P&L with IDs for independent reconciliation."""
        close_ids = sorted(self.ids.get("closes", set()))
        net = sum((self.events[event_id].net_pnl_inr or Decimal(0) for event_id in close_ids), Decimal(0))
        return {
            "stages": {stage: {
                "count": self.counts[stage],
                "event_ids": sorted(self.ids.get(stage, set())),
                "outcome_counts": dict(Counter(self.events[event_id].outcome
                                                for event_id in self.ids.get(stage, set()))),
            } for stage in FUNNEL_STAGES},
            "close_event_ids": close_ids,
            "realized_net_pnl_inr": str(net),
            "reconciles": self.reconciles(list(FUNNEL_STAGES)),
        }


def trace(setup: Setup, nodes: tuple[Node, Node, Node], c20: Cluster20, d1: float, d2: float, *,
          n4: Node | None = None, smma: dict[int, float | None] | None = None,
          fibonacci_barriers: list[float] | None = None, spread: float | None = None,
          exit_reason: str | None = None, event_id: str | None = None) -> dict:
    """Return a hash-sealed trade trace and explicitly expose any missing evidence (FB-2542)."""
    if not all(isfinite(value) and value >= 0 for value in (d1, d2)):
        raise ValueError("D1 and D2 must be finite and nonnegative")
    if spread is not None and (not isfinite(spread) or spread < 0):
        raise ValueError("spread must be finite and nonnegative")
    if smma is not None and any(
            period not in {8, 50, 100, 200} or (value is not None and not isfinite(value))
            for period, value in smma.items()):
        raise ValueError("SMMA evidence must use periods 8/50/100/200 and finite values")
    if fibonacci_barriers is not None and any(not isfinite(value) for value in fibonacci_barriers):
        raise ValueError("Fibonacci barrier values must be finite")
    if event_id is not None and not event_id.strip():
        raise ValueError("trace event ID cannot be empty")

    node_evidence = [{"side": n.side, "price": n.price, "pivot_at": n.pivot_at,
                      "confirmed_at": n.confirmed_at, "observable_at": n.observable_at,
                      "node_id": n.node_id, "parent_chain": list(n.parent_chain),
                      "source_bar_ids": list(n.source_bar_ids)} for n in (*nodes, *((n4,) if n4 else ()))]
    smma_evidence = ({str(period): smma.get(period) for period in (8, 50, 100, 200)}
                     if smma is not None else None)
    missing = []
    if n4 is None:
        missing.append("N4")
    if smma_evidence is None:
        missing.append("SMMA8_50_100_200")
    elif any(value is None for value in smma_evidence.values()):
        missing.append("SMMA_WARMUP_VALUE")
    if fibonacci_barriers is None:
        missing.append("FIBONACCI_BARRIERS")
    if spread is None:
        missing.append("SPREAD")
    if exit_reason is None or not exit_reason.strip():
        missing.append("EXIT_REASON")
    if event_id is None:
        missing.append("EVENT_ID")
    record: dict[str, object] = {
        "schema": "four-node-trade-trace-v1",
        "event_id": event_id,
        "status": setup.status,
        "side": setup.side,
        "passed": list(setup.passed),
        "failed": list(setup.failed),
        "nodes": node_evidence,
        "c20": {"low": c20.low, "mid": c20.mid, "high": c20.high},
        "smma": smma_evidence,
        "fibonacci_barriers": fibonacci_barriers,
        "d1": d1,
        "d2": d2,
        "spread": spread,
        "stop": setup.stop,
        "target": setup.target,
        "exit_reason": exit_reason,
        "missing_evidence": missing,
        "machine_verifiable": not missing,
    }
    record["record_sha256"] = hashlib.sha256(json.dumps(
        record, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()
    return record
