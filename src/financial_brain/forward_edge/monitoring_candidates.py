"""Non-promotable ORB, gap and chart-pattern candidate inventory (PE-0054)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .registry import (MechanismHypothesis, Registry, Status, StrategySpec, is_monitoring_family,
                       parameter_sha256)


class MonitoringCandidateError(ValueError):
    pass


@dataclass(frozen=True)
class MonitoringSignal:
    strategy_id: str
    as_of: str
    signal: str
    state: str = "MONITORING_ONLY"
    reason: str = "INSUFFICIENT_GOVERNED_PROSPECTIVE_EVIDENCE"


def candidate_specs() -> tuple[StrategySpec, ...]:
    root = Path(__file__).resolve().parents[1]
    source_paths = {
        "orb": root / "evaluation" / "intraday.py",
        "gap": root / "patterns" / "candles.py",
        "chart": root / "patterns" / "detectors.py",
        "directional": root / "evaluation" / "chart_setups.py",
    }
    code_digest = hashlib.sha256(b"".join(path.read_bytes() for path in source_paths.values())).hexdigest()
    definitions = (
        ("MON-ORB", "opening_range_breakout", "opening-range breakout",
         "15-minute range; confirmed complete-bar observation only",
         MechanismHypothesis("Opening price discovery can leave an early order-flow imbalance after overnight information is incorporated.",
                             "Institutional execution and gradual information diffusion may extend a move, but opening noise may dominate.",
                             "Late breakout entrants and liquidity providers exposed to adverse selection may bear losses.",
                             ("elevated opening imbalance without a circuit halt", "high early-session liquidity"),
                             "No positive matched excess remains after spread, impact and intraday same-time controls.")),
        ("MON-GAP-CONT", "gap_continuation", "unfilled-gap continuation",
         "range gap and corporate-action/circuit exclusions",
         MechanismHypothesis("An overnight gap can reflect information that is only partly incorporated at the open.",
                             "Slow interpretation of material news may sustain repricing; corporate actions and stale references can mimic it.",
                             "Late gap chasers and counterparties providing liquidity into informed flow may lose.",
                             ("material information gap", "normal continuous session after corporate-action checks"),
                             "After-cost returns do not exceed same-time gap controls once actions, halts and spread are handled.")),
        ("MON-GAP-FADE", "gap_reversal", "gap reversal or fill",
         "gap state, fill confirmation and complete-bar timestamps",
         MechanismHypothesis("Some opening gaps may overshoot when overnight liquidity is thin and transient orders dominate.",
                             "Temporary imbalance can mean-revert as continuous-session liquidity returns, but genuine news gaps should not.",
                             "Overnight information traders and liquidity providers may bear losses when genuine repricing is faded.",
                             ("non-news gap with restored continuous-session liquidity",),
                             "Reversal fails to outperform matched non-gap controls after costs or remains concentrated in genuine news gaps.")),
        ("MON-DIRECTIONAL-CHART", "directional_chart_pattern", "directional chart pattern",
         "direction from confirmed breakout/failure event only",
         MechanismHypothesis("Confirmed structure breaks may proxy persistent order flow or delayed response to public information.",
                             "Slow repositioning can extend the move, while crowded pattern definitions may decay quickly.",
                             "Late pattern followers and passive liquidity exposed to informed flow may pay.",
                             ("liquid continuous session with confirmed causal bars",),
                             "Predefined pattern events show no net advantage against a timestamp-matched placebo after costs.")),
        ("MON-CHART-PATTERN", "chart_pattern", "chart formation catalogue",
         "causal formation, confirmation and failure events",
         MechanismHypothesis("Repeated formations may summarize supply-demand imbalances and positioning constraints.",
                             "A persistent formation can reflect slow capital adjustment, but visual selection and crowding can erase it.",
                             "Pattern followers entering after public confirmation may pay through adverse selection and turnover.",
                             ("liquid, non-auction sessions with complete causal bars",),
                             "A fully specified detector fails chronological, costed comparison with matched placebo events.")),
    )
    specs = []
    for strategy_id, family, display, parameters, hypothesis in definitions:
        source = source_paths["orb"] if family == "opening_range_breakout" else (
            source_paths["gap"] if family.startswith("gap_") else source_paths["directional"] if "directional" in family
            else source_paths["chart"])
        specs.append(StrategySpec(
            strategy_id=strategy_id, family=family, version=1, code_sha256=code_digest,
            asset_scope=("NSE_EQ",), decision_clock="confirmed_bar_close", direction="LONG_SHORT",
            exit_rule="monitor_only_no_order", horizon_sessions=1,
            data_requirements=("complete_bars", "as_of_timestamp", "corporate_action_state", "liquidity_context"),
            source_lineage=f"{source.relative_to(root.parent.parent).as_posix()}::{display}",
            status=Status.SHADOW_ONLY,
            mechanism_hypothesis=hypothesis,
            parameter_sha256=parameter_sha256({"family": family, "monitoring_spec": parameters}),
        ))
    return tuple(specs)


def monitoring_signal(spec: StrategySpec, *, as_of: str, signal: str,
                      registry: Registry) -> MonitoringSignal:
    if not is_monitoring_family(spec.family):
        raise MonitoringCandidateError("strategy family is not on the monitoring-only policy list")
    if signal not in ("LONG", "SHORT", "NONE"):
        raise MonitoringCandidateError("monitoring signal must be LONG, SHORT, or NONE")
    try:
        current = registry.get(spec.strategy_id)
    except KeyError as exc:
        raise MonitoringCandidateError("monitoring candidate must be registered") from exc
    if current != spec or current.status is not Status.SHADOW_ONLY:
        raise MonitoringCandidateError("monitoring candidate must be the exact current SHADOW_ONLY registry version")
    return MonitoringSignal(spec.strategy_id, as_of, signal)
