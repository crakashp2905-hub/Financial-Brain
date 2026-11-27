"""Per-strategy net P&L explanation and covariance risk contribution (FE-0109)."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date
from typing import Mapping

import numpy as np


PNL_EVIDENCE_CLASSES = {
    "HISTORICAL",
    "HISTORICAL_PAPER",
    "LOCAL_PROSPECTIVE",
    "INDEPENDENTLY_AUDITABLE_FORWARD",
}


class StrategyAttributionError(ArithmeticError):
    """Strategy P&L components fail to reconcile or risk inputs are invalid."""


@dataclass(frozen=True)
class StrategyPnlComponents:
    strategy_id: str
    session: date
    realized_gross_pnl: float
    unrealized_pnl_change: float
    cash_yield: float
    slippage: float
    fees: float
    taxes: float
    financing: float
    other_charges: float
    ledger_net_pnl: float
    pnl_evidence_class: str

    def __post_init__(self) -> None:
        if not self.strategy_id.strip():
            raise StrategyAttributionError("strategy_id is required")
        if self.pnl_evidence_class not in PNL_EVIDENCE_CLASSES:
            raise StrategyAttributionError("P&L evidence class must be explicitly classified")
        values = (self.realized_gross_pnl, self.unrealized_pnl_change, self.cash_yield,
                  self.slippage, self.fees, self.taxes, self.financing,
                  self.other_charges, self.ledger_net_pnl)
        if any(not math.isfinite(value) for value in values):
            raise StrategyAttributionError("P&L components must be finite")
        if any(value < 0 for value in (self.slippage, self.fees, self.taxes,
                                        self.financing, self.other_charges)):
            raise StrategyAttributionError("cost components cannot be negative")

    @property
    def calculated_net_pnl(self) -> float:
        return (self.realized_gross_pnl + self.unrealized_pnl_change + self.cash_yield
                - self.slippage - self.fees - self.taxes - self.financing - self.other_charges)


@dataclass(frozen=True)
class StrategyAttributionReport:
    session: date
    pnl_evidence_class: str
    strategy_rows: tuple[dict[str, object], ...]
    portfolio_net_pnl: float
    portfolio_volatility: float
    ledger_reconciled: bool
    report_hash: str


def attribute_strategy_pnl(*, session: date,
                           components: tuple[StrategyPnlComponents, ...],
                           strategy_weights: Mapping[str, float],
                           covariance_matrix: tuple[tuple[float, ...], ...],
                           ledger_tolerance: float = 0.01) -> StrategyAttributionReport:
    """Explain every strategy's daily ledger P&L and calculate Euler risk contributions."""
    if not components or len({row.strategy_id for row in components}) != len(components):
        raise StrategyAttributionError("strategy components must be non-empty and unique")
    if any(row.session != session for row in components):
        raise StrategyAttributionError("all component rows must share the requested session")
    evidence_classes = {row.pnl_evidence_class for row in components}
    if len(evidence_classes) != 1:
        raise StrategyAttributionError("all strategy P&L rows must use one evidence class")
    evidence_class = next(iter(evidence_classes))
    strategy_ids = tuple(sorted(row.strategy_id for row in components))
    if set(strategy_weights) != set(strategy_ids):
        raise StrategyAttributionError("weights must include every and only every strategy")
    if any(not math.isfinite(value) or value < 0 for value in strategy_weights.values()):
        raise StrategyAttributionError("strategy weights must be finite and non-negative")
    if not math.isfinite(ledger_tolerance) or ledger_tolerance < 0:
        raise StrategyAttributionError("ledger tolerance must be finite and non-negative")
    matrix = np.asarray(covariance_matrix, dtype=float)
    n = len(strategy_ids)
    if matrix.shape != (n, n) or not np.isfinite(matrix).all():
        raise StrategyAttributionError("covariance matrix must match sorted strategy IDs")
    if not np.allclose(matrix, matrix.T, atol=1e-10) or float(np.linalg.eigvalsh(matrix).min()) < -1e-10:
        raise StrategyAttributionError("covariance matrix must be symmetric positive semidefinite")
    weight_vector = np.asarray([strategy_weights[key] for key in strategy_ids])
    variance = float(weight_vector @ matrix @ weight_vector)
    if variance < -1e-10:
        raise StrategyAttributionError("portfolio variance cannot be negative")
    portfolio_vol = math.sqrt(max(0.0, variance))
    marginal = matrix @ weight_vector
    rows_by_id = {row.strategy_id: row for row in components}
    result_rows = []
    reconciled = True
    for index, strategy_id in enumerate(strategy_ids):
        row = rows_by_id[strategy_id]
        difference = row.calculated_net_pnl - row.ledger_net_pnl
        match = abs(difference) <= ledger_tolerance
        reconciled = reconciled and match
        risk_contribution = (weight_vector[index] * marginal[index] / portfolio_vol
                             if portfolio_vol > 0 else 0.0)
        result_rows.append({"strategy_id": strategy_id,
                            "realized_gross_pnl": row.realized_gross_pnl,
                            "unrealized_pnl_change": row.unrealized_pnl_change,
                            "cash_yield": row.cash_yield,
                            "slippage": row.slippage, "fees": row.fees, "taxes": row.taxes,
                            "financing": row.financing, "other_charges": row.other_charges,
                            "calculated_net_pnl": row.calculated_net_pnl,
                            "ledger_net_pnl": row.ledger_net_pnl,
                            "pnl_evidence_class": row.pnl_evidence_class,
                            "ledger_difference": difference, "reconciled": match,
                            "weight": float(weight_vector[index]),
                            "risk_contribution": float(risk_contribution)})
    total_net = sum(row.calculated_net_pnl for row in components)
    fields = {"session": session.isoformat(), "pnl_evidence_class": evidence_class,
              "strategy_rows": result_rows,
              "portfolio_net_pnl": total_net, "portfolio_volatility": portfolio_vol,
              "ledger_reconciled": reconciled}
    digest = hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return StrategyAttributionReport(session, evidence_class, tuple(result_rows), total_net,
                                     portfolio_vol, reconciled, digest)


def require_ledger_reconciled(report: StrategyAttributionReport) -> None:
    if not report.ledger_reconciled:
        raise StrategyAttributionError("strategy P&L components do not reconcile to ledger postings")
