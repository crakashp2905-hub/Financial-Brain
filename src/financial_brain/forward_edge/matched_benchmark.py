"""Net strategy comparison that refuses alpha claims without matched exposures."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence


class MatchedBenchmarkError(ValueError):
    """Invalid strategy/benchmark return series or exposure description."""


def compare_matched_benchmark(*, benchmark_id: str,
                              strategy_net_returns: Sequence[float],
                              benchmark_net_returns: Sequence[float],
                              strategy_beta: float, benchmark_beta: float,
                              strategy_gross_leverage: float, benchmark_gross_leverage: float,
                              strategy_turnover: float, benchmark_turnover: float,
                              strategy_sector_weights: Mapping[str, float],
                              benchmark_sector_weights: Mapping[str, float],
                              beta_tolerance: float = 0.10,
                              leverage_tolerance: float = 0.10,
                              turnover_relative_tolerance: float = 0.20,
                              sector_weight_tolerance: float = 0.05) -> dict[str, object]:
    """Compare net daily returns only when market, sector, leverage, turnover match.

    Returns must be session-aligned and include all cash days. The benchmark return series
    must already represent the matching control portfolio; this function checks the supplied
    exposure match, but cannot establish benchmark construction or source provenance.
    """
    if not benchmark_id.strip():
        raise MatchedBenchmarkError("benchmark ID is required")
    if len(strategy_net_returns) != len(benchmark_net_returns) or len(strategy_net_returns) < 2:
        raise MatchedBenchmarkError("aligned strategy and benchmark need at least two daily returns")
    values = [*strategy_net_returns, *benchmark_net_returns, strategy_beta, benchmark_beta,
              strategy_gross_leverage, benchmark_gross_leverage, strategy_turnover,
              benchmark_turnover, beta_tolerance, leverage_tolerance,
              turnover_relative_tolerance, sector_weight_tolerance,
              *strategy_sector_weights.values(), *benchmark_sector_weights.values()]
    if any(not math.isfinite(float(value)) for value in values):
        raise MatchedBenchmarkError("returns, exposures and tolerances must be finite")
    if any(value <= -1 for value in (*strategy_net_returns, *benchmark_net_returns)):
        raise MatchedBenchmarkError("a daily return cannot be -100% or lower")
    if (min(strategy_gross_leverage, benchmark_gross_leverage,
            strategy_turnover, benchmark_turnover, beta_tolerance,
            leverage_tolerance, turnover_relative_tolerance, sector_weight_tolerance) < 0):
        raise MatchedBenchmarkError("leverage, turnover and tolerances cannot be negative")
    if any(weight < 0 for weight in (*strategy_sector_weights.values(),
                                     *benchmark_sector_weights.values())):
        raise MatchedBenchmarkError("sector weights cannot be negative")
    sector_keys = set(strategy_sector_weights) | set(benchmark_sector_weights)
    sector_gaps = {key: abs(strategy_sector_weights.get(key, 0.0)
                            - benchmark_sector_weights.get(key, 0.0)) for key in sorted(sector_keys)}
    beta_gap = abs(strategy_beta - benchmark_beta)
    leverage_gap = abs(strategy_gross_leverage - benchmark_gross_leverage)
    turnover_scale = max(abs(strategy_turnover), abs(benchmark_turnover), 1e-12)
    turnover_gap_relative = abs(strategy_turnover - benchmark_turnover) / turnover_scale
    reasons = []
    if beta_gap > beta_tolerance:
        reasons.append("MARKET_BETA_MISMATCH")
    if leverage_gap > leverage_tolerance:
        reasons.append("GROSS_LEVERAGE_MISMATCH")
    if turnover_gap_relative > turnover_relative_tolerance:
        reasons.append("TURNOVER_MISMATCH")
    if any(gap > sector_weight_tolerance for gap in sector_gaps.values()):
        reasons.append("SECTOR_MIX_MISMATCH")

    active = [float(s) - float(b) for s, b in zip(strategy_net_returns, benchmark_net_returns)]
    n = len(active)
    mean_active = sum(active) / n
    variance = sum((value - mean_active) ** 2 for value in active) / (n - 1)
    standard_error = math.sqrt(variance / n)
    strategy_total = math.prod(1 + float(value) for value in strategy_net_returns) - 1
    benchmark_total = math.prod(1 + float(value) for value in benchmark_net_returns) - 1
    matched = not reasons
    return {
        "benchmark_id": benchmark_id,
        "sessions": n,
        "strategy_compounded_net_return": strategy_total,
        "benchmark_compounded_net_return": benchmark_total,
        "compounded_active_return": strategy_total - benchmark_total,
        "exposure_match": {
            "status": "MATCHED" if matched else "UNMATCHED",
            "reasons": reasons,
            "beta_gap": beta_gap,
            "gross_leverage_gap": leverage_gap,
            "turnover_relative_gap": turnover_gap_relative,
            "sector_weight_gaps": sector_gaps,
        },
        "mean_daily_net_active_return": mean_active if matched else None,
        "daily_net_active_standard_error": standard_error if matched else None,
        "alpha_claim_allowed_by_exposure_check": matched,
        "limitations": ["benchmark construction and market-data provenance are caller supplied",
                        "simple paired standard error does not adjust for serial dependence",
                        "all returns must be net of costs and aligned on identical sessions"],
    }
