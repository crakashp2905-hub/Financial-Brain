"""After-cost futures carry comparison against matched alternatives (FE-0086)."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date


class CarryComparisonError(ValueError):
    """Invalid futures carry path or matched alternative."""


def _positive(value: float, name: str) -> None:
    if not math.isfinite(float(value)) or value <= 0:
        raise CarryComparisonError(f"{name} must be finite and positive")


@dataclass(frozen=True)
class FuturesCarryPath:
    observation_id: str
    session: date
    instrument_id: str
    sector: str
    liquidity_bucket: str
    direction: str
    days_held: int
    quantity: int
    entry_spot: float
    entry_future: float
    exit_spot: float
    exit_future: float
    notional: float
    spread_cost: float
    roll_cost: float
    financing_cost: float

    def __post_init__(self) -> None:
        if not all(v.strip() for v in (self.observation_id, self.instrument_id, self.sector,
                                        self.liquidity_bucket)):
            raise CarryComparisonError("carry path identity and strata are required")
        if self.direction not in {"LONG", "SHORT"}:
            raise CarryComparisonError("direction must be LONG or SHORT")
        if type(self.days_held) is not int or self.days_held < 1 or type(self.quantity) is not int or self.quantity < 1:
            raise CarryComparisonError("days_held and quantity must be positive integers")
        for name in ("entry_spot", "entry_future", "exit_spot", "exit_future", "notional"):
            _positive(getattr(self, name), name)
        for name in ("spread_cost", "roll_cost", "financing_cost"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0:
                raise CarryComparisonError(f"{name} must be finite and non-negative")

    @property
    def entry_basis(self) -> float:
        return self.entry_future - self.entry_spot

    @property
    def annualized_entry_basis(self) -> float:
        # Entry basis is a market descriptor; it is not itself the realized return.
        return self.entry_basis / self.entry_spot * 365.0 / self.days_held

    @property
    def gross_price_pnl(self) -> float:
        sign = 1.0 if self.direction == "LONG" else -1.0
        return sign * (self.exit_future - self.entry_future) * self.quantity

    @property
    def net_return(self) -> float:
        return (self.gross_price_pnl - self.spread_cost - self.roll_cost - self.financing_cost) / self.notional


@dataclass(frozen=True)
class CarryAlternative:
    observation_id: str
    session: date
    instrument_id: str
    sector: str
    liquidity_bucket: str
    direction: str
    days_held: int
    notional: float
    after_cost_net_return: float

    def __post_init__(self) -> None:
        if not all(v.strip() for v in (self.observation_id, self.instrument_id, self.sector,
                                        self.liquidity_bucket)):
            raise CarryComparisonError("alternative identity and strata are required")
        if self.direction not in {"LONG", "SHORT"} or type(self.days_held) is not int or self.days_held < 1:
            raise CarryComparisonError("alternative direction or holding period is invalid")
        _positive(self.notional, "notional")
        if not math.isfinite(float(self.after_cost_net_return)):
            raise CarryComparisonError("alternative return must be finite")


@dataclass(frozen=True)
class CarryPair:
    futures_id: str
    alternative_id: str
    entry_basis: float
    annualized_entry_basis: float
    futures_net_return: float
    alternative_net_return: float
    incremental_net_return: float
    match_distance: float


@dataclass(frozen=True)
class CarryComparisonReport:
    pairs: tuple[CarryPair, ...]
    unmatched_futures_ids: tuple[str, ...]
    unused_alternative_ids: tuple[str, ...]
    mean_incremental_net_return: float | None
    report_hash: str


def compare_futures_carry(paths: tuple[FuturesCarryPath, ...], alternatives: tuple[CarryAlternative, ...],
                          *, max_notional_gap: float = 0.25,
                          max_duration_gap_days: int = 2) -> CarryComparisonReport:
    """Match same-session direction/sector/liquidity alternatives; never discard unmatched paths."""
    if not math.isfinite(max_notional_gap) or not 0 <= max_notional_gap <= 1:
        raise CarryComparisonError("max_notional_gap must be between zero and one")
    if type(max_duration_gap_days) is not int or max_duration_gap_days < 0:
        raise CarryComparisonError("max_duration_gap_days must be a non-negative integer")
    ids = [item.observation_id for item in paths + alternatives]
    if len(ids) != len(set(ids)):
        raise CarryComparisonError("observation IDs must be unique")
    available = {item.observation_id: item for item in alternatives}
    pairs: list[CarryPair] = []
    unmatched: list[str] = []
    for path in sorted(paths, key=lambda item: (item.session, item.observation_id)):
        eligible = []
        for alternative in available.values():
            if (path.session != alternative.session or path.instrument_id == alternative.instrument_id
                    or path.direction != alternative.direction or path.sector != alternative.sector
                    or path.liquidity_bucket != alternative.liquidity_bucket):
                continue
            notional_gap = abs(alternative.notional / path.notional - 1.0)
            duration_gap = abs(alternative.days_held - path.days_held)
            if notional_gap <= max_notional_gap and duration_gap <= max_duration_gap_days:
                distance = notional_gap + duration_gap / max(path.days_held, alternative.days_held)
                eligible.append((distance, alternative.observation_id, alternative))
        if not eligible:
            unmatched.append(path.observation_id)
            continue
        distance, alt_id, alt = min(eligible, key=lambda row: (row[0], row[1]))
        del available[alt_id]
        pairs.append(CarryPair(path.observation_id, alt_id, path.entry_basis,
                               path.annualized_entry_basis, path.net_return,
                               alt.after_cost_net_return, path.net_return - alt.after_cost_net_return,
                               distance))
    mean = sum(pair.incremental_net_return for pair in pairs) / len(pairs) if pairs else None
    body = {"pairs": [{"futures_id": p.futures_id, "alternative_id": p.alternative_id,
                       "entry_basis": p.entry_basis, "annualized_entry_basis": p.annualized_entry_basis,
                       "futures_net_return": p.futures_net_return,
                       "alternative_net_return": p.alternative_net_return,
                       "incremental_net_return": p.incremental_net_return,
                       "match_distance": p.match_distance} for p in pairs],
            "unmatched_futures_ids": sorted(unmatched),
            "unused_alternative_ids": sorted(available), "mean_incremental_net_return": mean}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return CarryComparisonReport(tuple(pairs), tuple(sorted(unmatched)), tuple(sorted(available)), mean, digest)
