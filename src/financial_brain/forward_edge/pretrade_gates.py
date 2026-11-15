"""Frozen, evidence-linked pre-trade safety gates for paper intents."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
from zoneinfo import ZoneInfo

from financial_brain.forward_edge.market_data import NormalizedEvent, quote_quality_reasons


class PreTradeGateError(ValueError):
    """Invalid frozen pre-trade policy or evidence record."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IST = ZoneInfo("Asia/Kolkata")


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PreTradeGateError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _safe_number(value: float | None) -> float | str | None:
    if value is None:
        return None
    return value if type(value) in (int, float) and math.isfinite(value) else repr(value)


@dataclass(frozen=True)
class PreTradePolicy:
    strategy_sha256: str
    strategy_version: str
    frozen_at: datetime
    session_starts_at: datetime
    max_quote_age_s: float
    max_spread_bps: float
    max_adv_age_days: int
    max_adv_participation: float
    minimum_net_alpha_bps: float
    max_abs_exposure_inr: Decimal
    minimum_model_confidence: float

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.strategy_sha256) or not self.strategy_version.strip():
            raise PreTradeGateError("strategy identity and version are required")
        if _utc(self.frozen_at, "frozen_at") >= _utc(self.session_starts_at, "session_starts_at"):
            raise PreTradeGateError("pre-trade policy must be frozen before session start")
        for name, value in (("max_quote_age_s", self.max_quote_age_s),
                            ("max_spread_bps", self.max_spread_bps),
                            ("max_adv_participation", self.max_adv_participation),
                            ("minimum_net_alpha_bps", self.minimum_net_alpha_bps),
                            ("minimum_model_confidence", self.minimum_model_confidence)):
            if type(value) not in (int, float) or not math.isfinite(value):
                raise PreTradeGateError(f"{name} must be finite")
        if self.max_quote_age_s < 0 or self.max_spread_bps < 0:
            raise PreTradeGateError("quote age and spread limits must be non-negative")
        if type(self.max_adv_age_days) is not int or self.max_adv_age_days < 0:
            raise PreTradeGateError("maximum ADV age must be a non-negative integer")
        if not 0 < self.max_adv_participation <= 1:
            raise PreTradeGateError("ADV participation must be in (0, 1]")
        if not 0 <= self.minimum_model_confidence <= 1:
            raise PreTradeGateError("minimum model confidence must be in [0, 1]")
        try:
            exposure = Decimal(self.max_abs_exposure_inr)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise PreTradeGateError("exposure limit must be a finite positive Decimal") from exc
        if not exposure.is_finite() or exposure <= 0:
            raise PreTradeGateError("exposure limit must be a finite positive Decimal")
        object.__setattr__(self, "max_abs_exposure_inr", exposure)

    @property
    def sha256(self) -> str:
        body = {
            **asdict(self),
            "frozen_at": _utc(self.frozen_at, "frozen_at").isoformat(),
            "session_starts_at": _utc(self.session_starts_at, "session_starts_at").isoformat(),
            "max_abs_exposure_inr": str(self.max_abs_exposure_inr),
        }
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class PreTradeSnapshot:
    session_id: str
    strategy_sha256: str
    instrument: str
    side: str
    quantity: int
    decision_at: datetime
    quote: NormalizedEvent | None
    adv_quantity: int | None
    adv_as_of: date | None
    adv_source_sha256: str | None
    current_signed_exposure_inr: Decimal | None
    exposure_evidence_sha256: str | None
    gross_alpha_bps: float | None
    fees_bps: float | None
    spread_cost_bps: float | None
    impact_bps: float | None
    cost_evidence_sha256: str | None
    model_confidence: float | None
    model_evidence_sha256: str | None


@dataclass(frozen=True)
class GateCheck:
    name: str
    passed: bool
    reason: str
    evidence_sha256: str | None


@dataclass(frozen=True)
class PreTradeVerdict:
    approved: bool
    policy_sha256: str
    session_id: str
    instrument: str
    side: str
    quantity: int
    order_notional_inr: str | None
    expected_net_alpha_bps: float | None
    checks: tuple[GateCheck, ...]
    verdict_sha256: str

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(check.reason for check in self.checks if not check.passed)

    def as_risk_verdict(self) -> dict:
        return {
            "approved": self.approved,
            "reason": "PRETRADE_GATES_PASSED" if self.approved else "PRETRADE_GATES_BLOCKED",
            "reasons": list(self.reasons),
            "policy_sha256": self.policy_sha256,
            "verdict_sha256": self.verdict_sha256,
            "instrument": self.instrument,
            "side": self.side,
            "quantity": self.quantity,
            "order_notional_inr": self.order_notional_inr,
            "expected_net_alpha_bps": self.expected_net_alpha_bps,
            "checks": [asdict(check) for check in self.checks],
        }


def evaluate_pretrade(policy: PreTradePolicy, snapshot: PreTradeSnapshot) -> PreTradeVerdict:
    decision_at = _utc(snapshot.decision_at, "decision_at")
    side_ok = snapshot.side in {"BUY", "SELL"}
    identity_ok = (snapshot.strategy_sha256 == policy.strategy_sha256
                   and decision_at >= _utc(policy.session_starts_at, "session_starts_at"))
    instrument_ok = isinstance(snapshot.instrument, str) and bool(snapshot.instrument.strip())
    quantity_ok = type(snapshot.quantity) is int and snapshot.quantity > 0
    checks: list[GateCheck] = []

    def check(name: str, passed: bool, reason: str, evidence: str | None = None) -> None:
        checks.append(GateCheck(name, bool(passed), "OK" if passed else reason, evidence))

    check("frozen_policy", identity_ok, "POLICY_IDENTITY_OR_SESSION_MISMATCH")
    check("intent", side_ok and instrument_ok and quantity_ok, "INVALID_OR_INCOMPLETE_INTENT")

    quote = snapshot.quote
    quote_digest = quote.content_sha256() if isinstance(quote, NormalizedEvent) else None
    quote_reasons = (quote_quality_reasons(quote, intended_qty=snapshot.quantity,
                                           max_age_s=policy.max_quote_age_s,
                                           max_spread_bps=policy.max_spread_bps)
                     if isinstance(quote, NormalizedEvent) else ["MISSING_QUOTE"])
    quote_matches = (isinstance(quote, NormalizedEvent) and quote.instrument == snapshot.instrument
                     and quote.available_utc.astimezone(timezone.utc) <= decision_at)
    check("spread_and_quote_health", not quote_reasons and quote_matches,
          ",".join(quote_reasons) if quote_reasons else "QUOTE_IDENTITY_OR_AVAILABILITY_MISMATCH", quote_digest)

    notional: Decimal | None = None
    visible_qty = 0
    if quote is not None and isinstance(quote, NormalizedEvent):
        payload = dict(quote.payload)
        px_key, qty_key = (("ask_price", "ask_qty") if snapshot.side == "BUY"
                           else ("bid_price", "bid_qty"))
        try:
            price = Decimal(payload[px_key])
            visible_qty = int(payload[qty_key])
            if price.is_finite() and price > 0 and quantity_ok:
                notional = price * snapshot.quantity
        except (KeyError, TypeError, ValueError, InvalidOperation):
            pass
    adv_ok = (type(snapshot.adv_quantity) is int and snapshot.adv_quantity > 0
              and type(snapshot.adv_as_of) is date and snapshot.adv_source_sha256 is not None
              and _SHA256.fullmatch(snapshot.adv_source_sha256) is not None
              and (decision_at.astimezone(_IST).date() - snapshot.adv_as_of).days
              in range(0, policy.max_adv_age_days + 1))
    displayed_ok = quantity_ok and visible_qty >= snapshot.quantity
    participation_ok = quantity_ok and adv_ok and snapshot.quantity <= (
        snapshot.adv_quantity * policy.max_adv_participation)
    check("liquidity_and_capacity", displayed_ok and participation_ok,
          "LIQUIDITY_OR_ADV_CAPACITY_EVIDENCE_FAILED", snapshot.adv_source_sha256)

    costs = (snapshot.fees_bps, snapshot.spread_cost_bps, snapshot.impact_bps)
    cost_values_ok = all(type(value) in (int, float) and math.isfinite(value) and value >= 0 for value in costs)
    gross_ok = (type(snapshot.gross_alpha_bps) in (int, float)
                and math.isfinite(snapshot.gross_alpha_bps))
    net_alpha = (float(snapshot.gross_alpha_bps) - sum(float(v) for v in costs)
                 if gross_ok and cost_values_ok else None)
    cost_hash_ok = isinstance(snapshot.cost_evidence_sha256, str) and bool(
        _SHA256.fullmatch(snapshot.cost_evidence_sha256))
    cost_ok = (net_alpha is not None and net_alpha > policy.minimum_net_alpha_bps and cost_hash_ok)
    check("cost_to_net_alpha", cost_ok, "COSTED_NET_ALPHA_OR_COST_EVIDENCE_FAILED",
          snapshot.cost_evidence_sha256)

    exposure_ok = False
    if (notional is not None and isinstance(snapshot.current_signed_exposure_inr, Decimal)
            and snapshot.current_signed_exposure_inr.is_finite()
            and isinstance(snapshot.exposure_evidence_sha256, str)
            and _SHA256.fullmatch(snapshot.exposure_evidence_sha256)):
        signed = notional if snapshot.side == "BUY" else -notional
        projected = snapshot.current_signed_exposure_inr + signed
        exposure_ok = abs(projected) <= policy.max_abs_exposure_inr
    check("portfolio_exposure", exposure_ok, "EXPOSURE_LIMIT_OR_EVIDENCE_FAILED",
          snapshot.exposure_evidence_sha256)

    confidence_ok = (type(snapshot.model_confidence) in (int, float)
                     and math.isfinite(snapshot.model_confidence)
                     and policy.minimum_model_confidence <= snapshot.model_confidence <= 1
                     and isinstance(snapshot.model_evidence_sha256, str)
                     and _SHA256.fullmatch(snapshot.model_evidence_sha256) is not None)
    check("model_confidence", confidence_ok, "MODEL_CONFIDENCE_OR_EVIDENCE_FAILED",
          snapshot.model_evidence_sha256)

    facts = {
        "policy_sha256": policy.sha256,
        "snapshot": {
            "session_id": snapshot.session_id, "strategy_sha256": snapshot.strategy_sha256,
            "instrument": snapshot.instrument, "side": snapshot.side, "quantity": snapshot.quantity,
            "decision_at_utc": decision_at.isoformat(), "quote_sha256": quote_digest,
            "adv_quantity": snapshot.adv_quantity, "adv_as_of": snapshot.adv_as_of.isoformat()
            if isinstance(snapshot.adv_as_of, date) else None,
            "adv_source_sha256": snapshot.adv_source_sha256,
            "current_signed_exposure_inr": str(snapshot.current_signed_exposure_inr)
            if snapshot.current_signed_exposure_inr is not None else None,
            "exposure_evidence_sha256": snapshot.exposure_evidence_sha256,
            "gross_alpha_bps": _safe_number(snapshot.gross_alpha_bps),
            "fees_bps": _safe_number(snapshot.fees_bps),
            "spread_cost_bps": _safe_number(snapshot.spread_cost_bps),
            "impact_bps": _safe_number(snapshot.impact_bps),
            "cost_evidence_sha256": snapshot.cost_evidence_sha256,
            "model_confidence": _safe_number(snapshot.model_confidence),
            "model_evidence_sha256": snapshot.model_evidence_sha256,
        },
        "checks": [asdict(row) for row in checks],
    }
    verdict_hash = hashlib.sha256(json.dumps(facts, sort_keys=True, separators=(",", ":"),
                                             allow_nan=False).encode()).hexdigest()
    return PreTradeVerdict(all(check.passed for check in checks), policy.sha256, snapshot.session_id,
                           snapshot.instrument, snapshot.side, snapshot.quantity,
                           str(notional) if notional is not None else None, net_alpha,
                           tuple(checks), verdict_hash)
