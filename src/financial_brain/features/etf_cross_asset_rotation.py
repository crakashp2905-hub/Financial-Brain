"""Pre-registered cross-asset ETF rotation candidate and portfolio controls.

The calculator emits one point-in-time weight snapshot only. It is not a
backtester, does not score historical performance, and does not authorize an
order. The inverse-volatility control is an approximation that ignores
cross-asset covariance and trading costs.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone

from financial_brain.providers.etf_reference import ETFExposureCategory, ETFReferenceRecord

STRATEGY_ID = "ETF_CROSS_ASSET_ROTATION"
STRATEGY_VERSION = "1.0.0"
MOMENTUM_LOOKBACK = 126
VOLATILITY_LOOKBACK = 20
TARGET_ANNUAL_VOLATILITY = 0.10
MAX_GROSS_EXPOSURE = 1.0
SLEEVE_CATEGORIES = ("EQUITY", "DEBT", "GOLD", "SILVER")
_CATEGORY_MAP = {
    ETFExposureCategory.EQUITY_BROAD_MARKET: "EQUITY",
    ETFExposureCategory.EQUITY_SECTOR: "EQUITY",
    ETFExposureCategory.EQUITY_FACTOR: "EQUITY",
    ETFExposureCategory.BOND_DEBT: "DEBT",
    ETFExposureCategory.GOLD: "GOLD",
    ETFExposureCategory.SILVER: "SILVER",
}
SPEC = {
    "strategy_id": STRATEGY_ID,
    "version": STRATEGY_VERSION,
    "eligible_sleeves": SLEEVE_CATEGORIES,
    "within_sleeve_selection": "highest_126_session_close_to_close_return",
    "rotation_selection": "highest_sleeve_winner_126_session_return_if_positive_else_cash",
    "rotation_sizing": {"volatility_lookback_sessions": VOLATILITY_LOOKBACK,
                         "target_annual_volatility": TARGET_ANNUAL_VOLATILITY,
                         "annualization_sessions": 252,
                         "max_gross_exposure": MAX_GROSS_EXPOSURE},
    "controls": {
        "equal_volatility": "inverse_20_session_asset_volatility_normalized_to_full_gross; covariance_ignored",
        "buy_and_hold": "equal_weight_each_sleeve_winner_at_frozen_formation_snapshot",
        "same_risk": "equal_weight_control_scaled_to_10pct_annualized_20_session_portfolio_volatility_capped_at_100pct_gross",
        "cash": "zero_risk_free_control; all_weight_cash",
    },
    "input_policy": "exact_127_sourced_closes_timezone_aware_available_at_or_before_as_of_no_fill",
    "execution": "none_candidate_only",
    "costs": "not_modeled_in_candidate_snapshot",
}
SPEC_SHA256 = hashlib.sha256(json.dumps(SPEC, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ETFControlWeights:
    weights_by_isin: tuple[tuple[str, float], ...]
    cash_weight: float
    interpretation: str


@dataclass(frozen=True, slots=True)
class ETFCrossAssetRotationSnapshot:
    as_of: datetime
    sleeve_winners: tuple[tuple[str, str, float], ...]
    rotation_winner_isin: str | None
    rotation: ETFControlWeights
    equal_volatility_control: ETFControlWeights
    buy_and_hold_control: ETFControlWeights
    same_risk_control: ETFControlWeights
    cash_control: ETFControlWeights
    strategy_id: str = STRATEGY_ID
    strategy_version: str = STRATEGY_VERSION
    strategy_spec_sha256: str = SPEC_SHA256
    input_sha256: str = ""
    candidate_only: bool = True
    orders_authorized: bool = False


def _weights(values: Mapping[str, float], *, gross: float, explanation: str) -> ETFControlWeights:
    if not math.isfinite(gross) or not 0 <= gross <= MAX_GROSS_EXPOSURE:
        raise ValueError("gross exposure must be finite and between zero and one")
    total = sum(values.values())
    if not values or total <= 0 or gross == 0:
        return ETFControlWeights((), 1.0, explanation)
    normalized = tuple((key, gross * value / total) for key, value in sorted(values.items()))
    return ETFControlWeights(normalized, 1.0 - gross, explanation)


def build_cross_asset_rotation_snapshot(
    *,
    as_of: datetime,
    expected_sessions: Sequence[date],
    closes_by_isin: Mapping[str, Mapping[date, float]],
    source_hashes_by_isin: Mapping[str, Mapping[date, str]],
    available_at_by_isin: Mapping[str, Mapping[date, datetime]],
    references: Sequence[ETFReferenceRecord],
) -> ETFCrossAssetRotationSnapshot:
    """Build a candidate and four controls from exact point-in-time ETF closes.

    Select one ETF per sleeve using the same 126-session return rule. The
    rotation candidate holds the strongest positive sleeve winner; otherwise
    it is cash. `same_risk_control` is risk-targeted equal-weight over the
    current winners, not a claim of ex-post risk matching.
    """
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone aware")
    sessions = tuple(expected_sessions)
    if (not sessions or any(type(day) is not date for day in sessions)
            or tuple(sorted(set(sessions))) != sessions):
        raise ValueError("expected_sessions must be non-empty, unique and ordered")
    if sessions[-1] > as_of.date():
        raise ValueError("latest expected session cannot follow as_of")
    if len(sessions) <= MOMENTUM_LOOKBACK:
        raise ValueError("126-session rotation requires 127 expected closes")
    cutoff = as_of.astimezone(timezone.utc)
    selected_days = sessions[-(MOMENTUM_LOOKBACK + 1):]
    if len({row.isin for row in references}) != len(references):
        raise ValueError("reference ISINs must be unique")

    reference_by_isin: dict[str, ETFReferenceRecord] = {}
    for row in references:
        sleeve = _CATEGORY_MAP.get(row.exposure_category)
        if (sleeve is None or not row.underlying_index or row.effective_from > sessions[-1]
                or (row.effective_to is not None and sessions[-1] > row.effective_to)
                or row.published_at > cutoff or row.observed_at > cutoff):
            continue
        reference_by_isin[row.isin] = row

    eligible: list[tuple[str, str, float, float, tuple[str, ...], tuple[str, ...]]] = []
    for isin, reference in sorted(reference_by_isin.items()):
        closes = closes_by_isin.get(isin, {})
        hashes = source_hashes_by_isin.get(isin, {})
        availability = available_at_by_isin.get(isin, {})
        if any(day not in closes or day not in hashes or day not in availability for day in selected_days):
            continue
        values = [closes[day] for day in selected_days]
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError(f"{isin} close values must be finite and positive")
        digest_list = [hashes[day] for day in selected_days]
        if any(not isinstance(item, str) or len(item) != 64
               or any(char not in "0123456789abcdef" for char in item) for item in digest_list):
            raise ValueError(f"{isin} requires lowercase SHA-256 for every selected close")
        available_timestamps = [availability[day] for day in selected_days]
        if any(item.tzinfo is None or item.utcoffset() is None for item in available_timestamps):
            raise ValueError(f"{isin} availability timestamps must be timezone aware")
        if any(item.astimezone(timezone.utc) > cutoff for item in available_timestamps):
            continue
        daily_returns = [values[i] / values[i - 1] - 1 for i in range(1, len(values))]
        recent = daily_returns[-VOLATILITY_LOOKBACK:]
        if len(recent) != VOLATILITY_LOOKBACK:
            continue
        mean = sum(recent) / len(recent)
        daily_variance = sum((value - mean) ** 2 for value in recent) / (len(recent) - 1)
        annualized_vol = math.sqrt(daily_variance * 252)
        if not math.isfinite(annualized_vol) or annualized_vol <= 0:
            continue
        momentum = values[-1] / values[0] - 1
        eligible.append((isin, _CATEGORY_MAP[reference.exposure_category], momentum,
                         annualized_vol, tuple(digest_list),
                         tuple(item.astimezone(timezone.utc).isoformat() for item in available_timestamps)))

    best_by_sleeve: dict[str, tuple[str, float, float, tuple[str, ...], tuple[str, ...]]] = {}
    for isin, sleeve, momentum, vol, digests, timestamps in eligible:
        current = best_by_sleeve.get(sleeve)
        if current is None or (momentum, isin) > (current[1], current[0]):
            best_by_sleeve[sleeve] = (isin, momentum, vol, digests, timestamps)
    winners = sorted(best_by_sleeve.items())
    equal_values = {isin: 1 / vol for isin, _, vol, _, _ in best_by_sleeve.values()}
    equal_control = _weights(equal_values, gross=1.0,
                             explanation="Inverse 20-session volatility; ignores covariance, costs and tax.")
    buy_hold = _weights({isin: 1.0 for isin in equal_values}, gross=1.0,
                        explanation="Equal-weight sleeve winners at this frozen formation snapshot; freeze these weights for a buy-and-hold control.")

    same_risk_gross = 0.0
    if best_by_sleeve:
        aligned_returns = []
        for offset in range(1, VOLATILITY_LOOKBACK + 1):
            daily_by_asset = {}
            for isin, _, _, _, _, _ in eligible:
                vals = closes_by_isin[isin]
                end_index = len(sessions) - (VOLATILITY_LOOKBACK - offset)
                left, right = sessions[end_index - 2], sessions[end_index - 1]
                if left in vals and right in vals:
                    daily_by_asset[isin] = vals[right] / vals[left] - 1
            if all(isin in daily_by_asset for isin in best_by_sleeve):
                aligned_returns.append(sum(daily_by_asset[isin] for isin in best_by_sleeve) / len(best_by_sleeve))
        if len(aligned_returns) == VOLATILITY_LOOKBACK:
            mean = sum(aligned_returns) / len(aligned_returns)
            daily_variance = sum((value - mean) ** 2 for value in aligned_returns) / (len(aligned_returns) - 1)
            portfolio_vol = math.sqrt(daily_variance * 252)
            if portfolio_vol > 0 and math.isfinite(portfolio_vol):
                same_risk_gross = min(MAX_GROSS_EXPOSURE, TARGET_ANNUAL_VOLATILITY / portfolio_vol)
    same_risk = _weights({isin: 1.0 for isin in equal_values}, gross=same_risk_gross,
                         explanation="Equal-weight sleeve winners scaled to a 10% trailing 20-session portfolio volatility target and capped at 100% gross; ex-ante proxy only.")

    rotation_winner = None
    rotation = _weights({}, gross=0.0, explanation="No positive eligible sleeve winner; cash control.")
    if winners:
        sleeve, (isin, momentum, vol, _, _) = max(
            best_by_sleeve.items(), key=lambda item: (item[1][1], item[0], item[1][0]))
        if momentum > 0:
            rotation_winner = isin
            gross = min(MAX_GROSS_EXPOSURE, TARGET_ANNUAL_VOLATILITY / vol)
            rotation = _weights({isin: 1.0}, gross=gross,
                                explanation=f"Highest positive 126-session sleeve winner ({sleeve}); volatility-target proxy, before costs.")
    cash = ETFControlWeights((), 1.0, "Cash/no-action control; return series must be supplied by an authorized evaluator.")
    input_payload = {
        "spec_sha256": SPEC_SHA256,
        "as_of": cutoff.isoformat(),
        "sessions": [day.isoformat() for day in selected_days],
        "references": sorted((row.isin, row.source_sha256, row.observed_at.astimezone(timezone.utc).isoformat())
                             for row in reference_by_isin.values()),
        "inputs": sorted((isin, digests, times) for isin, _, _, _, digests, times in eligible),
    }
    input_digest = hashlib.sha256(json.dumps(input_payload, sort_keys=True, separators=(",", ":"),
                                             allow_nan=False).encode()).hexdigest()
    return ETFCrossAssetRotationSnapshot(
        as_of=cutoff,
        sleeve_winners=tuple((sleeve, values[0], values[1]) for sleeve, values in winners),
        rotation_winner_isin=rotation_winner,
        rotation=rotation,
        equal_volatility_control=equal_control,
        buy_and_hold_control=buy_hold,
        same_risk_control=same_risk,
        cash_control=cash,
        input_sha256=input_digest,
    )


__all__ = ["ETFControlWeights", "ETFCrossAssetRotationSnapshot", "SPEC", "SPEC_SHA256",
           "STRATEGY_ID", "STRATEGY_VERSION", "build_cross_asset_rotation_snapshot"]
