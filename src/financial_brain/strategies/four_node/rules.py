"""Four-Node predicates, entry, stops, trailing, exits, Indian conversion and funnel (FB-2515 to FB-2542).

Every predicate returns a reason code. If the variant is not executable (unresolved ambiguities) the
setup is BLOCKED. Entry is never filled at the breakout bar's close: the order is actionable on the
next bar. Distances are in "units" — pips for the XAU reference, ATR multiples for India.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

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
             d1_inclusive: bool = True, d2_inclusive: bool = False) -> Setup:
    """Evaluate a completed bar ``bar`` closing at ``close`` against N1..N3 (all observable)."""
    if not v.executable() and v.executable_intent:
        return Setup("BLOCKED", None, (), tuple(f"unresolved:{u}" for u in v.unresolved()))
    n1, n2, n3 = nodes
    if any(n.observable_at is None or n.observable_at >= bar for n in nodes):
        return Setup("REJECTED", None, (), ("node_not_observable_before_breakout_bar",))
    side = _direction(n1, n2, n3)
    if side is None:
        return Setup("REJECTED", None, (), ("not_zigzag",))
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

    def on_price(self, executable_price: float) -> float:
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


def round_stop(stop: float, tick: float, side: str) -> float:
    """Round a stop to tick *away* from entry so rounding never tightens risk (FB-2520)."""
    import math
    n = stop / tick
    return (math.ceil(n - 1e-9) if side == "SELL" else math.floor(n + 1e-9)) * tick


def trail_price(side: str, bid: float, ask: float) -> float:
    """Trail on the executable exit side: a long exits at the bid, a short at the ask (FB-2523)."""
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


def size_by_risk(capital_at_risk: float, entry: float, stop: float, cfg: InstrumentConfig,
                 lot: int = 1) -> int:
    """Units from rupee risk budget, rounded down to whole lots (FB-2526). Never the forex X rule."""
    per_unit = abs(entry - stop) * cfg.multiplier + cfg.spread + cfg.slippage
    if per_unit <= 0:
        raise ValueError("zero risk per unit")
    units = int(capital_at_risk // per_unit)
    return units - units % lot


@dataclass
class Funnel:
    """raw pivots -> confirmed patterns -> setups -> intents -> fills -> closes (FB-2541)."""

    counts: Counter = field(default_factory=Counter)
    ids: dict[str, set[str]] = field(default_factory=dict)

    def add(self, stage: str, event_id: str) -> None:
        if event_id in self.ids.setdefault(stage, set()):
            return
        self.ids[stage].add(event_id)
        self.counts[stage] += 1

    def reconciles(self, order: list[str]) -> bool:
        return all(self.ids.get(b, set()) <= self.ids.get(a, set()) | self.ids.get(b, set())
                   and self.counts[b] <= self.counts[a] for a, b in zip(order, order[1:], strict=False))


def trace(setup: Setup, nodes: tuple[Node, Node, Node], c20: Cluster20, d1: float, d2: float) -> dict:
    """Per-trade evidence record (FB-2542)."""
    return {"status": setup.status, "side": setup.side, "passed": list(setup.passed),
            "failed": list(setup.failed),
            "nodes": [{"side": n.side, "price": n.price, "pivot_at": n.pivot_at,
                       "confirmed_at": n.confirmed_at, "observable_at": n.observable_at,
                       "node_id": n.node_id, "parent_chain": list(n.parent_chain),
                       "source_bar_ids": list(n.source_bar_ids)} for n in nodes],
            "c20": {"low": c20.low, "mid": c20.mid, "high": c20.high},
            "d1": d1, "d2": d2, "stop": setup.stop, "target": setup.target}
