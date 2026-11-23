"""Pre-registered cross-sectional ETF momentum ranking; candidate research only.

The rule compares ETFs only when source-backed reference records identify the same
underlying TRI and exposure category. It emits no order, return forecast or edge claim.
"""
from __future__ import annotations

import math
import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone

from financial_brain.providers.etf_reference import ETFExposureCategory, ETFReferenceRecord

HORIZONS_SESSIONS = (21, 63, 126, 252)
STRATEGY_ID = "ETF_CS_MOMENTUM"
STRATEGY_VERSION = "1.0.0"
_SPEC = {
    "strategy_id": STRATEGY_ID,
    "strategy_version": STRATEGY_VERSION,
    "horizons_sessions": HORIZONS_SESSIONS,
    "horizon_weights": "equal",
    "comparison_group": ["exchange", "underlying_index_tri", "exposure_category"],
    "missing_endpoint_policy": "exclude_without_fill_or_bridge",
    "rank_tie_policy": "average_rank",
}
STRATEGY_SPEC_SHA256 = hashlib.sha256(
    json.dumps(_SPEC, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


@dataclass(frozen=True, slots=True)
class ETFMomentumCandidate:
    strategy_id: str
    strategy_version: str
    strategy_spec_sha256: str
    isin: str
    symbol: str
    underlying_index: str
    exposure_category: ETFExposureCategory
    as_of: date
    horizon_returns: tuple[tuple[int, float], ...]
    composite_percentile: float
    price_input_sha256: str


def rank_etf_momentum(
    *,
    as_of: datetime,
    expected_sessions: Sequence[date],
    closes_by_isin: Mapping[str, Mapping[date, float]],
    source_hashes_by_isin: Mapping[str, Mapping[date, str]],
    references: Sequence[ETFReferenceRecord],
) -> list[ETFMomentumCandidate]:
    """Rank comparable ETFs on fixed 21/63/126/252-session returns.

    Missing prices or source hashes are not filled or bridged. Reference facts must already be
    published and observed by ``as_of`` and effective on the final session. Unknown
    exposures and records without an underlying TRI are excluded from ranking.
    """
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone aware")
    sessions = tuple(sorted(set(expected_sessions)))
    if not sessions or sessions[-1] > as_of.date():
        raise ValueError("expected sessions must be non-empty and cannot follow as_of")
    if len(sessions) <= max(HORIZONS_SESSIONS):
        return []
    cutoff = as_of.astimezone(timezone.utc)
    groups: dict[
        tuple[str, ETFExposureCategory, str],
        list[tuple[ETFReferenceRecord, dict[int, float], dict[date, str]]],
    ] = defaultdict(list)
    end = sessions[-1]
    reference_ids = [row.isin for row in references]
    if len(reference_ids) != len(set(reference_ids)):
        raise ValueError("ETF references must have unique ISINs")
    for reference in references:
        if (reference.exposure_category is ETFExposureCategory.UNKNOWN or not reference.underlying_index
                or reference.effective_from > end
                or (reference.effective_to is not None and end > reference.effective_to)
                or reference.published_at > cutoff or reference.observed_at > cutoff):
            continue
        closes = closes_by_isin.get(reference.isin, {})
        source_hashes = source_hashes_by_isin.get(reference.isin, {})
        if any(day not in sessions for day in closes):
            raise ValueError(f"{reference.isin} close series contains an unsourced session")
        returns: dict[int, float] = {}
        used_hashes: dict[date, str] = {}
        valid = True
        for horizon in HORIZONS_SESSIONS:
            start = closes.get(sessions[-1 - horizon])
            finish = closes.get(end)
            if start is None or finish is None:
                valid = False
                break
            for session in (sessions[-1 - horizon], end):
                source_hash = source_hashes.get(session)
                if source_hash is None:
                    valid = False
                    break
                if (len(source_hash) != 64 or
                        any(char not in "0123456789abcdef" for char in source_hash)):
                    raise ValueError(f"{reference.isin} source hashes must be lowercase SHA-256")
                used_hashes[session] = source_hash
            if not valid:
                break
            if (not math.isfinite(start) or not math.isfinite(finish) or start <= 0 or finish <= 0):
                raise ValueError(f"{reference.isin} closes must be finite positive values")
            returns[horizon] = finish / start - 1.0
        if valid:
            group = (reference.underlying_index.strip().casefold(), reference.exposure_category,
                     reference.exchange)
            groups[group].append((reference, returns, used_hashes))

    ranked: list[ETFMomentumCandidate] = []
    for (_, exposure, _), members in groups.items():
        if len(members) < 2:
            continue
        percentile_scores: dict[str, list[float]] = {row.isin: [] for row, _, _ in members}
        for horizon in HORIZONS_SESSIONS:
            ordered = sorted(members, key=lambda item: (-item[1][horizon], item[0].isin))
            i = 0
            while i < len(ordered):
                j = i + 1
                while j < len(ordered) and ordered[j][1][horizon] == ordered[i][1][horizon]:
                    j += 1
                average_rank = ((i + 1) + j) / 2
                percentile = (len(ordered) - average_rank) / (len(ordered) - 1)
                for reference, _, _ in ordered[i:j]:
                    percentile_scores[reference.isin].append(percentile)
                i = j
        for reference, returns, used_hashes in members:
            ranked.append(ETFMomentumCandidate(
                isin=reference.isin, symbol=reference.symbol,
                strategy_id=STRATEGY_ID, strategy_version=STRATEGY_VERSION,
                strategy_spec_sha256=STRATEGY_SPEC_SHA256,
                underlying_index=reference.underlying_index or "",
                exposure_category=exposure, as_of=end,
                horizon_returns=tuple((h, returns[h]) for h in HORIZONS_SESSIONS),
                composite_percentile=sum(percentile_scores[reference.isin]) / len(HORIZONS_SESSIONS),
                price_input_sha256=hashlib.sha256(json.dumps(
                    [(day.isoformat(), used_hashes[day]) for day in sorted(used_hashes)],
                    separators=(",", ":")
                ).encode("utf-8")).hexdigest(),
            ))
    return sorted(ranked, key=lambda item: (-item.composite_percentile, item.isin))


__all__ = ["ETFMomentumCandidate", "HORIZONS_SESSIONS", "STRATEGY_ID", "STRATEGY_SPEC_SHA256",
           "STRATEGY_VERSION", "rank_etf_momentum"]
