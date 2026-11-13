"""Non-promotable ORB, gap and chart-pattern candidate inventory (PE-0054)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .registry import Registry, Status, StrategySpec, is_monitoring_family, parameter_sha256


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
         "15-minute range; confirmed complete-bar observation only"),
        ("MON-GAP-CONT", "gap_continuation", "unfilled-gap continuation",
         "range gap and corporate-action/circuit exclusions"),
        ("MON-GAP-FADE", "gap_reversal", "gap reversal or fill",
         "gap state, fill confirmation and complete-bar timestamps"),
        ("MON-DIRECTIONAL-CHART", "directional_chart_pattern", "directional chart pattern",
         "direction from confirmed breakout/failure event only"),
        ("MON-CHART-PATTERN", "chart_pattern", "chart formation catalogue",
         "causal formation, confirmation and failure events"),
    )
    specs = []
    for strategy_id, family, display, parameters in definitions:
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
