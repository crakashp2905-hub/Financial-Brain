"""Deterministic portfolio disaster scenarios and fail-closed operating invariants (FE-0112)."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping


class DisasterStressError(ValueError):
    """Invalid portfolio or disaster scenario input."""


class DisasterKind(StrEnum):
    GAP = "GAP"
    LIMIT_DOWN = "LIMIT_DOWN"
    SPREAD_BLOWOUT = "SPREAD_BLOWOUT"
    EXPIRY_SHOCK = "EXPIRY_SHOCK"
    NETWORK_PARTITION = "NETWORK_PARTITION"


REQUIRED_DISASTER_KINDS = frozenset(DisasterKind)


@dataclass(frozen=True)
class StressPosition:
    instrument_id: str
    signed_quantity: float
    mark_price: float
    multiplier: float = 1.0

    def __post_init__(self) -> None:
        if not self.instrument_id.strip():
            raise DisasterStressError("position instrument ID is required")
        if not all(math.isfinite(value) for value in
                   (self.signed_quantity, self.mark_price, self.multiplier)):
            raise DisasterStressError("position values must be finite")
        if self.mark_price <= 0 or self.multiplier <= 0:
            raise DisasterStressError("mark price and contract multiplier must be positive")


@dataclass(frozen=True)
class DisasterScenario:
    scenario_id: str
    kind: DisasterKind
    price_shocks: Mapping[str, float]
    spread_blowout_bps: float = 0.0
    expiry_extra_loss: Mapping[str, float] | None = None
    exit_fill_fraction: float = 1.0
    network_partition: bool = False

    def __post_init__(self) -> None:
        if not self.scenario_id.strip():
            raise DisasterStressError("scenario ID is required")
        if not math.isfinite(self.spread_blowout_bps) or self.spread_blowout_bps < 0:
            raise DisasterStressError("spread blowout bps must be finite and non-negative")
        if not math.isfinite(self.exit_fill_fraction) or not 0 <= self.exit_fill_fraction <= 1:
            raise DisasterStressError("exit fill fraction must be in [0, 1]")
        if any(not math.isfinite(value) or value < -1 for value in self.price_shocks.values()):
            raise DisasterStressError("price return shocks must be finite and at least -100%")
        if self.expiry_extra_loss and any(not math.isfinite(value) or value < 0
                                          for value in self.expiry_extra_loss.values()):
            raise DisasterStressError("expiry losses must be finite and non-negative")
        if self.kind is DisasterKind.NETWORK_PARTITION and not self.network_partition:
            raise DisasterStressError("network partition scenario must activate the outage invariant")
        if self.kind is DisasterKind.SPREAD_BLOWOUT and self.spread_blowout_bps <= 0:
            raise DisasterStressError("spread blowout scenario needs a positive spread shock")


@dataclass(frozen=True)
class DisasterScenarioResult:
    scenario_id: str
    kind: DisasterKind
    mark_pnl: float
    execution_cost: float
    expiry_loss: float
    net_pnl: float
    gross_exposure: float
    unfilled_exit_exposure: float
    new_entries_allowed: bool
    requires_reconciliation: bool


@dataclass(frozen=True)
class DisasterStressReport:
    starting_equity: float
    results: tuple[DisasterScenarioResult, ...]
    worst_net_pnl: float
    worst_loss_fraction: float
    breaches_loss_limit: bool
    report_hash: str


def evaluate_disaster_stress(*, positions: tuple[StressPosition, ...],
                             scenarios: tuple[DisasterScenario, ...],
                             starting_equity: float, max_loss_fraction: float) -> DisasterStressReport:
    """Stress a frozen position snapshot; outputs are deterministic scenarios, not forecasts."""
    if not math.isfinite(starting_equity) or starting_equity <= 0:
        raise DisasterStressError("starting equity must be finite and positive")
    if not math.isfinite(max_loss_fraction) or not 0 <= max_loss_fraction <= 1:
        raise DisasterStressError("maximum loss fraction must be in [0, 1]")
    if not positions or not scenarios:
        raise DisasterStressError("positions and disaster scenarios must be non-empty")
    if len({position.instrument_id for position in positions}) != len(positions):
        raise DisasterStressError("position instrument IDs must be unique")
    if len({scenario.scenario_id for scenario in scenarios}) != len(scenarios):
        raise DisasterStressError("scenario IDs must be unique")
    absent = REQUIRED_DISASTER_KINDS - {scenario.kind for scenario in scenarios}
    if absent:
        raise DisasterStressError("required disaster scenario kinds missing: " +
                                  ", ".join(sorted(kind.value for kind in absent)))

    output: list[DisasterScenarioResult] = []
    for scenario in sorted(scenarios, key=lambda item: item.scenario_id):
        gross = mark_pnl = costs = expiry_loss = unfilled = 0.0
        for position in positions:
            exposure = abs(position.signed_quantity * position.mark_price * position.multiplier)
            gross += exposure
            shock = scenario.price_shocks.get(position.instrument_id,
                                               scenario.price_shocks.get("*"))
            if shock is None:
                raise DisasterStressError(
                    f"scenario {scenario.scenario_id} has no price shock for {position.instrument_id}")
            mark_pnl += position.signed_quantity * position.mark_price * position.multiplier * shock
            costs += exposure * scenario.exit_fill_fraction * scenario.spread_blowout_bps / 10_000
            unfilled += exposure * (1 - scenario.exit_fill_fraction)
            if scenario.expiry_extra_loss:
                expiry_loss += scenario.expiry_extra_loss.get(position.instrument_id, 0.0)
        net = mark_pnl - costs - expiry_loss
        output.append(DisasterScenarioResult(
            scenario.scenario_id, scenario.kind, mark_pnl, costs, expiry_loss, net, gross, unfilled,
            not scenario.network_partition, scenario.network_partition))

    worst = min((row.net_pnl for row in output), default=0.0)
    loss_fraction = max(0.0, -worst / starting_equity)
    payload = {"starting_equity": starting_equity, "max_loss_fraction": max_loss_fraction,
               "results": [{"scenario_id": row.scenario_id, "kind": row.kind.value,
                            "mark_pnl": row.mark_pnl, "execution_cost": row.execution_cost,
                            "expiry_loss": row.expiry_loss, "net_pnl": row.net_pnl,
                            "gross_exposure": row.gross_exposure,
                            "unfilled_exit_exposure": row.unfilled_exit_exposure,
                            "new_entries_allowed": row.new_entries_allowed,
                            "requires_reconciliation": row.requires_reconciliation}
                           for row in output]}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return DisasterStressReport(starting_equity, tuple(output), worst, loss_fraction,
                                loss_fraction > max_loss_fraction, digest)
