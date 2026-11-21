"""Portfolio-level decisions and risk (FE-0104 .. FE-0113). Paper only; the kill switch cannot be reset by an agent."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from financial_brain.forward_edge.approvals import Approval, ApprovalError, validate_approval


class RiskError(ValueError):
    pass


# ---- FE-0104 frozen allocation ----

@dataclass(frozen=True)
class Allocation:
    weights: tuple[tuple[str, Decimal], ...]  # includes "CASH"

    def __post_init__(self) -> None:
        if sum((w for _, w in self.weights), Decimal(0)) != Decimal(1):
            raise RiskError("allocation weights must sum to exactly 1 including CASH")
        if "CASH" not in {n for n, _ in self.weights}:
            raise RiskError("allocation must name CASH explicitly")
        if any(w < 0 for _, w in self.weights):
            raise RiskError("weights cannot be negative")

    def sha256(self) -> str:
        body = json.dumps([[n, str(w)] for n, w in sorted(self.weights)], separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


def combined_result(allocation: Allocation, book_returns: dict[str, Decimal]) -> Decimal:
    """Every funded book is in the combined result; a losing book cannot be silently dropped."""
    names = {n for n, _ in allocation.weights if n != "CASH"}
    if set(book_returns) != names:
        raise RiskError("returns must be supplied for every funded book, no more and no fewer")
    return sum((w * book_returns.get(n, Decimal(0)) for n, w in allocation.weights), Decimal(0))


# ---- FE-0105 binding limits across simultaneous intents ----

@dataclass(frozen=True)
class Limits:
    max_gross: Decimal
    max_net: Decimal
    max_single_name: Decimal
    max_sector: Decimal


def check_batch(limits: Limits, intents: list[dict], positions: dict[str, Decimal], sectors: dict[str, str]) -> list[dict]:
    """Evaluate intents cumulatively, in order. Two intents that are each fine alone cannot jointly breach a limit."""
    book = dict(positions)
    verdicts = []
    for it in intents:
        sym, signed = it["symbol"], Decimal(it["notional"]) * (1 if it["side"] == "BUY" else -1)
        trial = dict(book)
        trial[sym] = trial.get(sym, Decimal(0)) + signed
        gross = sum(abs(v) for v in trial.values())
        net = sum(trial.values())
        sector = sectors.get(sym, "?")
        sector_exp = sum(abs(v) for s, v in trial.items() if sectors.get(s, "?") == sector)
        reasons = []
        if gross > limits.max_gross:
            reasons.append("GROSS")
        if abs(net) > limits.max_net:
            reasons.append("NET")
        if abs(trial[sym]) > limits.max_single_name:
            reasons.append("SINGLE_NAME")
        if sector_exp > limits.max_sector:
            reasons.append("SECTOR")
        if reasons:
            verdicts.append({"symbol": sym, "decision": "NO_TRADE", "reasons": reasons})
        else:
            book = trial
            verdicts.append({"symbol": sym, "decision": "ALLOW", "reasons": []})
    return verdicts


# ---- FE-0106 vol target, CVaR, concentration ----

def vol_target_scale(realized_vol: float, target_vol: float, cap: float = 1.0) -> float:
    if realized_vol <= 0 or target_vol <= 0 or cap <= 0:
        raise RiskError("vol inputs must be positive")
    return min(cap, target_vol / realized_vol)


def cvar(returns: list[float], alpha: float = 0.05) -> float:
    if not returns or not 0 < alpha < 1:
        raise RiskError("returns and alpha required")
    k = max(1, int(len(returns) * alpha))
    worst = sorted(returns)[:k]
    return sum(worst) / k


def concentration_penalty(weights: list[float]) -> float:
    """Herfindahl index of absolute weights: 1/n (diversified) to 1 (one name)."""
    total = sum(abs(w) for w in weights)
    if total == 0:
        return 0.0
    return sum((abs(w) / total) ** 2 for w in weights)


# ---- FE-0107 exposure book ----

class ExposureBook:
    def __init__(self, available_margin: Decimal) -> None:
        self.available = available_margin
        self._reserved: dict[str, Decimal] = {}

    def reserve(self, order_id: str, amount: Decimal) -> None:
        if order_id in self._reserved:
            raise RiskError("order already reserved")
        if amount > self.available - sum(self._reserved.values(), Decimal(0)):
            raise RiskError("insufficient unreserved margin: cannot be double-allocated")
        self._reserved[order_id] = amount

    def release(self, order_id: str) -> None:
        self._reserved.pop(order_id)

    def unreserved(self) -> Decimal:
        return self.available - sum(self._reserved.values(), Decimal(0))


def worst_case_spread_loss(width: Decimal, credit: Decimal, quantity: int) -> Decimal:
    """Short-vertical worst case: (width - credit) * quantity."""
    if width <= 0 or credit < 0 or credit > width or quantity <= 0:
        raise RiskError("invalid spread")
    return (width - credit) * quantity


# ---- FE-0108 persistent kill switch ----

class KillSwitch:
    def __init__(self, path: Path) -> None:
        self.path = path

    def tripped(self) -> bool:
        return self.path.exists() and json.loads(self.path.read_text(encoding="utf-8")).get("state") == "TRIPPED"

    def trip(self, reason: str) -> None:
        self.path.write_text(json.dumps({"state": "TRIPPED", "reason": reason}), encoding="utf-8")

    def reset(self, approval: Approval, *, now: datetime, scope_sha256: str) -> None:
        """Only a valid human DISARM_RESET approval can reset. Agents and LLMs cannot."""
        try:
            validate_approval(approval, kind="DISARM_RESET", subject="kill_switch", scope_sha256=scope_sha256, now=now)
        except ApprovalError as exc:
            raise RiskError(f"reset refused: {exc}") from exc
        self.path.write_text(json.dumps({"state": "CLEAR", "reset_by": approval.actor_id}), encoding="utf-8")


# ---- FE-0109 attribution ----

def attribute(events: list[dict]) -> dict[str, Decimal]:
    """events: {'kind': REALIZED|UNREALIZED|FEES|SLIPPAGE|CASH_YIELD, 'amount': Decimal}. Equity change must equal the sum."""
    out: dict[str, Decimal] = {}
    for e in events:
        out[e["kind"]] = out.get(e["kind"], Decimal(0)) + Decimal(e["amount"])
    out["EQUITY_CHANGE"] = sum((v for k, v in out.items()), Decimal(0))
    return out


# ---- FE-0110 versioned throttle ----

@dataclass(frozen=True)
class ThrottlePolicy:
    version: int
    breakpoints: tuple[tuple[float, float], ...]  # (vol_above, scale)

    def scale(self, vol: float) -> float:
        s = 1.0
        for above, scale in sorted(self.breakpoints):
            if vol >= above:
                s = scale
        return s

    def sha256(self) -> str:
        return hashlib.sha256(json.dumps([self.version, sorted(self.breakpoints)]).encode("utf-8")).hexdigest()


# ---- FE-0111 capacity ----

def capacity_notional(adv_notional: Decimal, depth_notional: Decimal, participation_cap: Decimal) -> Decimal:
    return min(adv_notional * participation_cap, depth_notional)


# ---- FE-0112 shocks ----

def apply_shock(cash: Decimal, positions: dict[str, tuple[int, Decimal]], shock: dict[str, Decimal]) -> dict[str, Decimal]:
    """positions: sym -> (qty, price). shock: sym -> fractional move. Cash is untouched; only marks move."""
    before = cash + sum(q * p for q, p in positions.values())
    after_positions = {s: (q, p * (Decimal(1) + shock.get(s, Decimal(0)))) for s, (q, p) in positions.items()}
    after = cash + sum(q * p for q, p in after_positions.values())
    explained = sum(q * p * shock.get(s, Decimal(0)) for s, (q, p) in positions.items())
    return {"equity_before": before, "equity_after": after, "explained_change": explained,
            "invariant_holds": (after - before) == explained and cash >= Decimal(0)}


# ---- FE-0113 explicit decisions ----

def decide(*, liquidity_ok: bool, margin_known: bool, margin_sufficient: bool) -> dict[str, str]:
    if not liquidity_ok:
        return {"decision": "NO_TRADE", "reason": "INSUFFICIENT_LIQUIDITY"}
    if not margin_known:
        return {"decision": "NO_TRADE", "reason": "UNCERTAIN_MARGIN"}
    if not margin_sufficient:
        return {"decision": "NO_TRADE", "reason": "INSUFFICIENT_MARGIN"}
    return {"decision": "TRADE", "reason": "OK"}
