"""Deterministic same-session size/risk matched control comparisons (FE-0082)."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date


class PairedControlError(ValueError):
    """Invalid candidate/control observation or matching policy."""


@dataclass(frozen=True)
class EvaluationObservation:
    observation_id: str
    session: date
    instrument_id: str
    side: str
    sector: str
    liquidity_bucket: str
    notional: float
    ex_ante_risk: float
    net_return: float

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.observation_id, self.instrument_id,
                                                self.sector, self.liquidity_bucket)):
            raise PairedControlError("observation identity and match strata are required")
        if self.side not in {"BUY", "SELL"}:
            raise PairedControlError("side must be BUY or SELL")
        for name, value in (("notional", self.notional), ("ex_ante_risk", self.ex_ante_risk),
                            ("net_return", self.net_return)):
            if not math.isfinite(float(value)):
                raise PairedControlError(f"{name} must be finite")
        if self.notional <= 0 or self.ex_ante_risk <= 0:
            raise PairedControlError("notional and ex-ante risk must be positive")


@dataclass(frozen=True)
class MatchedPair:
    candidate_id: str
    control_id: str
    session: date
    candidate_net_return: float
    control_net_return: float
    incremental_net_return: float
    match_distance: float


@dataclass(frozen=True)
class PairedControlReport:
    pairs: tuple[MatchedPair, ...]
    unmatched_candidate_ids: tuple[str, ...]
    unused_control_ids: tuple[str, ...]
    mean_incremental_net_return: float | None
    report_hash: str


def compare_matched_controls(candidates: tuple[EvaluationObservation, ...],
                             controls: tuple[EvaluationObservation, ...], *,
                             max_relative_size_gap: float = 0.25,
                             max_relative_risk_gap: float = 0.25) -> PairedControlReport:
    """Greedily pair each candidate once to its closest eligible same-session control.

    Matching uses only pre-outcome fields. The observed net returns enter only after
    pairing; exact session/sector/liquidity/side strata and distinct instruments are required.
    """
    for name, value in (("max_relative_size_gap", max_relative_size_gap),
                        ("max_relative_risk_gap", max_relative_risk_gap)):
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise PairedControlError(f"{name} must be between zero and one")
    ids = [item.observation_id for item in candidates + controls]
    if len(ids) != len(set(ids)):
        raise PairedControlError("observation IDs must be unique across candidates and controls")
    available = {item.observation_id: item for item in controls}
    pairs: list[MatchedPair] = []
    unmatched: list[str] = []
    for candidate in sorted(candidates, key=lambda item: (item.session, item.observation_id)):
        eligible: list[tuple[float, str, EvaluationObservation]] = []
        for control in available.values():
            if (control.session != candidate.session or control.instrument_id == candidate.instrument_id
                    or control.side != candidate.side or control.sector != candidate.sector
                    or control.liquidity_bucket != candidate.liquidity_bucket):
                continue
            size_gap = abs(control.notional / candidate.notional - 1.0)
            risk_gap = abs(control.ex_ante_risk / candidate.ex_ante_risk - 1.0)
            if size_gap <= max_relative_size_gap and risk_gap <= max_relative_risk_gap:
                eligible.append((size_gap + risk_gap, control.observation_id, control))
        if not eligible:
            unmatched.append(candidate.observation_id)
            continue
        distance, control_id, control = min(eligible, key=lambda item: (item[0], item[1]))
        del available[control_id]
        pairs.append(MatchedPair(candidate.observation_id, control_id, candidate.session,
                                 candidate.net_return, control.net_return,
                                 candidate.net_return - control.net_return, distance))
    mean = sum(pair.incremental_net_return for pair in pairs) / len(pairs) if pairs else None
    body = {"pairs": [{"candidate_id": p.candidate_id, "control_id": p.control_id,
                       "session": p.session.isoformat(), "candidate_net_return": p.candidate_net_return,
                       "control_net_return": p.control_net_return,
                       "incremental_net_return": p.incremental_net_return,
                       "match_distance": p.match_distance} for p in pairs],
            "unmatched_candidate_ids": sorted(unmatched),
            "unused_control_ids": sorted(available), "mean_incremental_net_return": mean}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return PairedControlReport(tuple(pairs), tuple(sorted(unmatched)), tuple(sorted(available)), mean, digest)
