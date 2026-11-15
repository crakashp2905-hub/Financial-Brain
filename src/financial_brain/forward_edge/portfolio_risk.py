"""Portfolio-level decisions and risk (FE-0104 .. FE-0113). Paper only; the kill switch cannot be reset by an agent."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from math import sqrt
from pathlib import Path

import numpy as np

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
    book: dict[str, Decimal] = dict(positions)
    verdicts = []
    for it in intents:
        sym = str(it["symbol"])
        notional = Decimal(str(it["notional"]))
        signed: Decimal = notional if it["side"] == "BUY" else -notional
        trial = dict(book)
        trial[sym] = trial.get(sym, Decimal(0)) + signed
        gross = sum((abs(v) for v in trial.values()), Decimal(0))
        net = sum(trial.values(), Decimal(0))
        sector = sectors.get(sym, "?")
        sector_exp = sum((abs(v) for s, v in trial.items() if sectors.get(s, "?") == sector), Decimal(0))
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


def aggregate_correlated_books(*, book_exposures: dict[str, dict[str, Decimal]],
                               factor_names: tuple[str, ...], covariance: np.ndarray,
                               max_market_exposure: Decimal, max_portfolio_volatility: Decimal,
                               market_factor: str = "MARKET") -> dict:
    """Aggregate simultaneous strategy books before checking account-wide risk ceilings.

    Exposures are signed factor notionals. Correlated strategy books therefore add together
    inside the quadratic covariance calculation; they are never treated as independent books.
    """
    names = tuple(factor_names)
    if (len(set(names)) != len(names) or not names or market_factor not in names
            or not book_exposures or any(not key for key in book_exposures)):
        raise RiskError("unique factor names, a market factor and named book exposures are required")
    if (not isinstance(covariance, np.ndarray) or covariance.shape != (len(names), len(names))
            or not np.isfinite(covariance).all() or not np.allclose(covariance, covariance.T, atol=1e-10)
            or float(np.linalg.eigvalsh(covariance).min()) < -1e-8):
        raise RiskError("factor covariance must be finite, symmetric, positive semidefinite and aligned")
    if (not isinstance(max_market_exposure, Decimal) or not max_market_exposure.is_finite()
            or max_market_exposure <= 0 or not isinstance(max_portfolio_volatility, Decimal)
            or not max_portfolio_volatility.is_finite() or max_portfolio_volatility <= 0):
        raise RiskError("market-exposure and portfolio-volatility ceilings must be positive")
    for book_id, exposures in book_exposures.items():
        if set(exposures) != set(names) or any(not isinstance(v, Decimal) or not v.is_finite()
                                                for v in exposures.values()):
            raise RiskError(f"book {book_id} must provide finite Decimal exposure for every factor")
    aggregate = {name: sum((book[name] for book in book_exposures.values()), Decimal(0)) for name in names}
    vector = np.array([float(aggregate[name]) for name in names], dtype=float)
    variance = max(0.0, float(vector @ covariance @ vector))
    volatility = Decimal(str(sqrt(variance)))
    market = abs(aggregate[market_factor])
    reasons = []
    if market > max_market_exposure:
        reasons.append("MARKET_EXPOSURE_LIMIT")
    if volatility > max_portfolio_volatility:
        reasons.append("CORRELATED_PORTFOLIO_RISK_LIMIT")
    return {"decision": "NO_TRADE" if reasons else "ALLOW", "reasons": reasons,
            "aggregate_factor_exposures": {key: str(value) for key, value in aggregate.items()},
            "market_exposure": str(market), "portfolio_volatility": str(volatility),
            "book_count": len(book_exposures)}


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


def assess_order_capacity(*, order_notional: Decimal, adv_notional: Decimal | None,
                          adv_as_of: date | None, adv_source_sha256: str | None,
                          depth_notional: Decimal | None, depth_observed_at: datetime | None,
                          depth_source_sha256: str | None, order_at: datetime,
                          max_adv_age_days: int, max_depth_age: timedelta,
                          participation_cap: Decimal, safety_factor: Decimal) -> dict:
    """Fail closed unless an order fits dated ADV and fresh, source-identified visible depth.

    The margin-of-safety factor is applied after taking the tighter of the ADV participation
    limit and displayed depth. Oversized orders are withheld; this function never silently
    resizes a strategy decision.
    """
    def valid_digest(value: str | None) -> bool:
        return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)

    if (not isinstance(order_notional, Decimal) or not order_notional.is_finite() or order_notional <= 0
            or not isinstance(participation_cap, Decimal) or not participation_cap.is_finite()
            or not 0 < participation_cap <= 1
            or not isinstance(safety_factor, Decimal) or not safety_factor.is_finite()
            or not 0 < safety_factor <= 1):
        raise RiskError("order notional, participation cap and safety factor are invalid")
    if (type(max_adv_age_days) is not int or max_adv_age_days < 0
            or not isinstance(max_depth_age, timedelta) or max_depth_age < timedelta(0)
            or not isinstance(order_at, datetime) or order_at.tzinfo is None or order_at.utcoffset() is None):
        raise RiskError("capacity freshness limits and order timestamp are invalid")
    missing = (adv_notional is None or adv_as_of is None or not valid_digest(adv_source_sha256)
               or depth_notional is None or depth_observed_at is None or not valid_digest(depth_source_sha256))
    if missing:
        return {"decision": "NO_TRADE", "reason": "MISSING_CAPACITY_EVIDENCE",
                "order_notional": str(order_notional), "allowed_notional": "0"}
    assert adv_notional is not None and adv_as_of is not None
    assert depth_notional is not None and depth_observed_at is not None
    if (not isinstance(adv_notional, Decimal) or not adv_notional.is_finite() or adv_notional <= 0
            or not isinstance(depth_notional, Decimal) or not depth_notional.is_finite() or depth_notional < 0
            or not isinstance(adv_as_of, date) or isinstance(adv_as_of, datetime)
            or not isinstance(depth_observed_at, datetime) or depth_observed_at.tzinfo is None
            or depth_observed_at.utcoffset() is None):
        raise RiskError("capacity evidence values or timestamps are invalid")
    order_utc = order_at.astimezone(timezone.utc)
    depth_utc = depth_observed_at.astimezone(timezone.utc)
    adv_age = order_utc.date() - adv_as_of
    depth_age = order_utc - depth_utc
    if adv_as_of > order_utc.date() or adv_age.days > max_adv_age_days:
        return {"decision": "NO_TRADE", "reason": "STALE_ADV_EVIDENCE",
                "order_notional": str(order_notional), "allowed_notional": "0"}
    if depth_age < timedelta(0) or depth_age > max_depth_age:
        return {"decision": "NO_TRADE", "reason": "STALE_OR_FUTURE_DEPTH_EVIDENCE",
                "order_notional": str(order_notional), "allowed_notional": "0"}
    base_capacity = capacity_notional(adv_notional, depth_notional, participation_cap)
    allowed = base_capacity * safety_factor
    return {"decision": "ALLOW" if order_notional <= allowed else "NO_TRADE",
            "reason": "WITHIN_OBSERVED_CAPACITY" if order_notional <= allowed else "CAPACITY_EXCEEDED",
            "order_notional": str(order_notional), "allowed_notional": str(allowed),
            "adv_as_of": adv_as_of.isoformat(), "depth_observed_at_utc": depth_utc.isoformat(),
            "adv_source_sha256": adv_source_sha256, "depth_source_sha256": depth_source_sha256,
            "participation_cap": str(participation_cap), "safety_factor": str(safety_factor)}


# ---- FE-0112 shocks ----

def apply_shock(cash: Decimal, positions: dict[str, tuple[int, Decimal]],
                shock: dict[str, Decimal]) -> dict[str, Decimal | bool]:
    """positions: sym -> (qty, price). shock: sym -> fractional move. Cash is untouched; only marks move."""
    before = cash + sum(q * p for q, p in positions.values())
    after_positions = {s: (q, p * (Decimal(1) + shock.get(s, Decimal(0)))) for s, (q, p) in positions.items()}
    after = cash + sum(q * p for q, p in after_positions.values())
    explained = sum((q * p * shock.get(s, Decimal(0)) for s, (q, p) in positions.items()), Decimal(0))
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
