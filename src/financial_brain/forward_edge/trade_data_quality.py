"""Attribute trades to observed pre-entry feed, spread, and volatility conditions."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math


class TradeDataQualityError(ValueError):
    """Malformed market-quality policy, trade, or outage evidence."""


def _utc(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise TradeDataQualityError(f"{label} must be a timezone-aware ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TradeDataQualityError(f"{label} must be a timezone-aware ISO timestamp")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class DataQualityPolicy:
    policy_id: str
    max_quote_age_seconds: float
    max_spread_bp: float
    high_volatility_percentile: float
    frozen_at_utc: str
    observation_start_utc: str
    observation_end_utc: str

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise TradeDataQualityError("policy_id is required")
        for name in ("max_quote_age_seconds", "max_spread_bp", "high_volatility_percentile"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise TradeDataQualityError(f"{name} must be finite and nonnegative")
        if self.high_volatility_percentile > 1:
            raise TradeDataQualityError("high_volatility_percentile cannot exceed 1")
        frozen = _utc(self.frozen_at_utc, "frozen_at_utc")
        start = _utc(self.observation_start_utc, "observation_start_utc")
        end = _utc(self.observation_end_utc, "observation_end_utc")
        if not frozen < start < end:
            raise TradeDataQualityError("policy must be frozen before a nonempty observation window")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()


def _finite(value, label: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise TradeDataQualityError(f"{label} must be finite")
    result = float(value)
    if minimum is not None and result < minimum:
        raise TradeDataQualityError(f"{label} must be at least {minimum}")
    return result


def assess_trade_data_quality(
    *, trades: Sequence[Mapping[str, object]],
    feed_gaps: Sequence[Mapping[str, object]],
    policy: DataQualityPolicy,
) -> dict:
    """Label every closed trade, retain evidence IDs, and summarize net results by quality class.

    A row can be classified ``CLEAN_OBSERVED`` only when quote, spread, volatility,
    and feed-gap coverage are all present. ``SYSTEM_ISSUE`` is a data-quality tag,
    not a causal judgment or a declaration that the strategy failed.
    """
    gaps = []
    gap_ids = set()
    for event in feed_gaps:
        if not isinstance(event, Mapping):
            raise TradeDataQualityError("feed-gap events must be mappings")
        event_id, source = event.get("event_id"), event.get("source")
        if not isinstance(event_id, str) or not event_id.strip() or event_id in gap_ids:
            raise TradeDataQualityError("feed-gap event IDs must be unique and nonempty")
        if not isinstance(source, str) or not source.strip():
            raise TradeDataQualityError("feed-gap source is required")
        start = _utc(event.get("started_at_utc"), "gap started_at_utc")
        end = _utc(event.get("ended_at_utc"), "gap ended_at_utc")
        if end < start:
            raise TradeDataQualityError("feed-gap end precedes start")
        gap_ids.add(event_id)
        gaps.append({"event_id": event_id, "source": source, "start": start, "end": end})

    seen = set()
    classified = []
    for trade in trades:
        if not isinstance(trade, Mapping):
            raise TradeDataQualityError("trades must be mappings")
        trade_id, strategy = trade.get("trade_id"), trade.get("strategy")
        if not isinstance(trade_id, str) or not trade_id.strip() or trade_id in seen:
            raise TradeDataQualityError("trade IDs must be unique and nonempty")
        if not isinstance(strategy, str) or not strategy.strip():
            raise TradeDataQualityError("strategy is required")
        seen.add(trade_id)
        decided = _utc(trade.get("decision_at_utc"), "decision_at_utc")
        entry = _utc(trade.get("entry_at_utc"), "entry_at_utc")
        if decided >= entry:
            raise TradeDataQualityError("decision must precede entry")
        if not (_utc(policy.observation_start_utc, "observation_start_utc") <= entry
                < _utc(policy.observation_end_utc, "observation_end_utc")):
            raise TradeDataQualityError("trade entry is outside the frozen observation window")
        net_pnl = _finite(trade.get("net_pnl_inr"), "net_pnl_inr")
        coverage = trade.get("feed_gap_coverage_complete")
        if type(coverage) is not bool:
            raise TradeDataQualityError("feed_gap_coverage_complete must be boolean")
        reasons, evidence_ids = [], []
        if not coverage:
            reasons.append("FEED_GAP_COVERAGE_INCOMPLETE")
        else:
            for gap in gaps:
                if gap["start"] <= entry and gap["end"] >= decided:
                    reasons.append("FEED_GAP_OVERLAPS_DECISION_TO_ENTRY")
                    evidence_ids.append(gap["event_id"])
        quote_age = trade.get("quote_age_seconds")
        spread = trade.get("spread_bp")
        volatility = trade.get("volatility_percentile")
        quote_evidence_id = trade.get("quote_evidence_id")
        volatility_evidence_id = trade.get("volatility_evidence_id")
        evidence_by_issue: dict[str, list[str]] = {}
        quality_ids_valid = all(isinstance(value, str) and value.strip()
                                for value in (quote_evidence_id, volatility_evidence_id))
        if quote_age is None or spread is None or volatility is None or not quality_ids_valid:
            reasons.append("QUOTE_SPREAD_OR_VOLATILITY_EVIDENCE_MISSING")
        else:
            quote_age = _finite(quote_age, "quote_age_seconds", minimum=0)
            spread = _finite(spread, "spread_bp", minimum=0)
            volatility = _finite(volatility, "volatility_percentile", minimum=0)
            if volatility > 1:
                raise TradeDataQualityError("volatility_percentile cannot exceed 1")
            if quote_age > policy.max_quote_age_seconds:
                reasons.append("STALE_QUOTE")
                evidence_by_issue["STALE_QUOTE"] = [quote_evidence_id]
            if spread > policy.max_spread_bp:
                reasons.append("ABNORMAL_SPREAD")
                evidence_by_issue["ABNORMAL_SPREAD"] = [quote_evidence_id]
            if volatility >= policy.high_volatility_percentile:
                reasons.append("HIGH_VOLATILITY")
                evidence_by_issue["HIGH_VOLATILITY"] = [volatility_evidence_id]
        incomplete = (not coverage or quote_age is None or spread is None or volatility is None
                      or not quality_ids_valid)
        status = "EVIDENCE_INCOMPLETE" if incomplete else ("SYSTEM_ISSUE" if reasons else "CLEAN_OBSERVED")
        classified.append({"trade_id": trade_id, "strategy": strategy, "entry_at_utc": entry.isoformat(),
                           "net_pnl_inr": net_pnl, "status": status,
                           "issue_reasons": reasons, "feed_gap_evidence_ids": sorted(set(evidence_ids)),
                           "evidence_ids_by_issue": evidence_by_issue})
    if not classified:
        raise TradeDataQualityError("trade population is required")
    groups = {}
    for status in ("SYSTEM_ISSUE", "CLEAN_OBSERVED", "EVIDENCE_INCOMPLETE"):
        rows = [row for row in classified if row["status"] == status]
        groups[status] = {"trades": len(rows), "net_pnl_inr": sum(row["net_pnl_inr"] for row in rows),
                          "losing_trades": sum(row["net_pnl_inr"] < 0 for row in rows)}
    return {"policy_id": policy.policy_id, "policy_sha256": policy.sha256,
            "trade_count": len(classified), "groups": groups, "trades": classified,
            "interpretation": "Market-data quality strata are descriptive and do not by themselves establish strategy failure or causality."}
