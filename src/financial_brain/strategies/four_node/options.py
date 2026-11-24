"""Auditable Four-Node underlying-thesis to long-option candidate routing (FB-2528)."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from financial_brain.paper.options import OptionPaperError
from financial_brain.paper.options_plan import OptionContract, build_long_option_plan
from financial_brain.kernel.units import OptionLots

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class UnderlyingThesis:
    signal_id: str
    instrument_id: str
    side: Literal["BUY", "SELL"]
    decided_at: datetime
    underlying_price: float
    invalidation_price: float
    source_sha256: str

    def __post_init__(self) -> None:
        if not self.signal_id.strip() or not self.instrument_id.strip():
            raise ValueError("underlying signal and instrument IDs are required")
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("underlying side must be BUY or SELL")
        if self.decided_at.tzinfo is None or self.decided_at.utcoffset() is None:
            raise ValueError("underlying decision time must be timezone-aware")
        if (not math.isfinite(self.underlying_price) or not math.isfinite(self.invalidation_price)
                or self.underlying_price <= 0 or self.invalidation_price <= 0):
            raise ValueError("underlying and invalidation prices must be finite and positive")
        if not _SHA256.fullmatch(self.source_sha256):
            raise ValueError("underlying source hash must be lowercase SHA-256")


@dataclass(frozen=True)
class OptionCandidateTerms:
    contract: OptionContract
    stop_premium: float
    target_premium: float


def route_underlying_thesis(
    thesis: UnderlyingThesis,
    candidates: tuple[OptionCandidateTerms, ...],
    *,
    lots: int | OptionLots,
    max_quote_age: timedelta,
) -> dict[str, object]:
    """Generate contract-qualified CE/PE paper plans without conflating option P&L and thesis.

    Direction maps BUY→CE and SELL→PE only to propose candidates. Candidate plans still require
    an exact observed quote, depth, premium risk levels and an after-cost target. This function
    does not rank multiple eligible strikes or place orders.
    """
    if not isinstance(thesis, UnderlyingThesis):
        raise TypeError("typed underlying thesis is required")
    if not isinstance(candidates, tuple):
        raise TypeError("option candidates must be a frozen tuple")
    if not isinstance(max_quote_age, timedelta) or max_quote_age < timedelta(0):
        raise ValueError("max_quote_age must be a nonnegative duration")
    intended_type = "CE" if thesis.side == "BUY" else "PE"
    thesis_record = {
        "record_type": "UNDERLYING_THESIS", "signal_id": thesis.signal_id,
        "instrument_id": thesis.instrument_id, "side": thesis.side,
        "decided_at": thesis.decided_at.isoformat(),
        "underlying_price": thesis.underlying_price,
        "invalidation_price": thesis.invalidation_price,
        "source_sha256": thesis.source_sha256,
    }
    thesis_hash = hashlib.sha256(json.dumps(
        thesis_record, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()
    plans: list[dict[str, object]] = []
    rejected: list[dict[str, str]] = []
    seen_tokens: set[int] = set()
    candidate_evidence: list[dict[str, object]] = []
    for terms in candidates:
        contract = terms.contract
        token = contract.quote.token
        candidate_evidence.append({
            "token": token,
            "symbol": contract.quote.symbol,
            "underlying": contract.underlying.strip().upper(),
            "option_type": contract.option_type,
            "strike": contract.strike,
            "expiry": contract.quote.expiry.isoformat(),
            "observed_at": contract.quote.observed_at.isoformat(),
            "bid": contract.quote.bid,
            "ask": contract.quote.ask,
            "bid_qty": contract.quote.bid_qty,
            "ask_qty": contract.quote.ask_qty,
            "stop_premium": terms.stop_premium,
            "target_premium": terms.target_premium,
        })
        if token in seen_tokens:
            rejected.append({"instrument": contract.quote.symbol, "reason": "DUPLICATE_CONTRACT_TOKEN"})
            continue
        seen_tokens.add(token)
        if contract.underlying.strip().upper() != thesis.instrument_id.strip().upper():
            rejected.append({"instrument": contract.quote.symbol, "reason": "UNDERLYING_ID_MISMATCH"})
            continue
        if contract.option_type != intended_type:
            rejected.append({"instrument": contract.quote.symbol, "reason": "DIRECTION_CONTRACT_MISMATCH"})
            continue
        try:
            plan = build_long_option_plan(
                contract, decision_at=thesis.decided_at,
                underlying_price=thesis.underlying_price,
                invalidation_price=thesis.invalidation_price, lots=lots,
                stop_premium=terms.stop_premium, target_premium=terms.target_premium,
                max_quote_age=max_quote_age,
            )
        except (OptionPaperError, ValueError) as exc:
            rejected.append({"instrument": contract.quote.symbol,
                             "reason": "OPTION_PLAN_REJECTED", "detail": str(exc)})
            continue
        plans.append(plan)

    option_record = {
        "record_type": "OPTION_CANDIDATE_DECISION",
        "decision_id": hashlib.sha256(json.dumps({
            "underlying_thesis_sha256": thesis_hash,
            "candidate_evidence": sorted(candidate_evidence, key=lambda row: (
                int(row["token"]), str(row["symbol"])
            )),
            "plans": plans,
            "rejected_candidates": rejected,
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "parent_signal_id": thesis.signal_id,
        "underlying_thesis_sha256": thesis_hash,
        "intended_option_type": intended_type,
        "status": "CANDIDATES_AVAILABLE" if plans else "NO_TRADE",
        "plans": plans,
        "rejected_candidates": rejected,
        "candidate_selection": "NOT_RANKED_OR_SELECTED",
        "underlying_return_is_option_pnl": False,
        "broker_order_created": False,
    }
    return {"underlying_thesis": thesis_record, "option_decision": option_record}
