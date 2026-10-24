"""Trader-readable research report with explicit evidence boundaries."""
from __future__ import annotations

from .inverse_strategy import InferenceResult


def strategy_report(result: InferenceResult, rules: dict, regime: dict,
                    *, paper_performance: dict | None = None,
                    net_after_costs: dict | None = None) -> dict:
    if rules["source_model"] != result.model_name:
        raise ValueError("rule source does not match inferred model")
    validated = paper_performance is not None and net_after_costs is not None
    return {"strategy": {"model": result.model_name, "training_period": result.split,
                         "validation_action_metrics": result.validation,
                         "independent_action_holdout": result.holdout,
                         "majority_action_baseline": result.majority_baseline,
                         "rule_version": rules["version_hash"],
                         "rule_fidelity": rules["validation_fidelity"],
                         "regime_outcomes": regime,
                         "paper_performance": paper_performance,
                         "after_costs": net_after_costs,
                         "health": "requires_market_validation" if not validated else "review_required",
                         "limitations": ["Action accuracy is not market alpha.",
                                         "Surrogate fidelity is not profitability.",
                                         "No autonomous live order authorization."]},
            "opportunity": None,
            "warning": "Backtested estimates are not guaranteed future returns."}


def opportunity_report(estimate, *, supporters: list[str], opponents: list[str],
                       analogues: list[dict] | None = None) -> dict:
    if not estimate.invalidation:
        raise ValueError("opportunity requires explicit invalidation")
    return {"instrument": estimate.instrument, "direction": estimate.direction,
            "detected_by": estimate.strategy, "supporting_strategies": supporters,
            "opposing_strategies": opponents, "regime": estimate.regime,
            "expected_net_alpha": estimate.net_alpha,
            "reliability": estimate.reliability,
            "historical_analogues": analogues or [],
            "execution_cost_estimate": estimate.costs,
            "tail_risk": estimate.tail_risk,
            "invalidation": list(estimate.invalidation),
            "supporting_evidence": list(estimate.supporting_evidence),
            "contradictory_evidence": list(estimate.contradictory_evidence),
            "warning": "Research estimate, not a guaranteed return or live-order instruction."}
