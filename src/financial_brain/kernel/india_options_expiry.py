"""Effective-dated India option expiry rules and delivery-solvency preflight."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from .options_paper import OptionsPaperAccount


class IndiaExpiryError(ValueError):
    """Invalid Indian option expiry rule or preflight evidence."""


@dataclass(frozen=True)
class IndiaExpiryRule:
    rule_id: str
    venue: str
    underlying_kind: str
    effective_from: date
    settlement: str
    exercise_style: str
    source_refs: tuple[str, ...]


@dataclass(frozen=True)
class DeliveryMarginEvidence:
    contract_id: str
    margin_per_lot: Decimal
    source_id: str
    source_sha256: str


@dataclass(frozen=True)
class ExpiryReadiness:
    status: str
    session_date: date
    account_id: str
    settlement_source_sha256: str
    settlement_prices_sha256: str
    account_state_sha256: str
    rulebook_sha256: str
    delivery_margin_sha256: str
    cash_outflow_inr: str
    delivery_margin_inr: str
    ending_cash_inr: str
    physical_share_deltas: tuple[tuple[str, int], ...]
    blockers: tuple[str, ...]
    readiness_sha256: str


RULES = (
    IndiaExpiryRule(
        "india-index-options-cash-european-v1", "NFO", "INDEX",
        date(2000, 12, 11), "CASH", "EUROPEAN",
        ("https://www.sebi.gov.in/sebi_data/attachdocs/1300707082606.pdf",),
    ),
    IndiaExpiryRule(
        "india-single-stock-options-physical-european-v1", "NFO", "STOCK",
        date(2019, 10, 1), "PHYSICAL", "EUROPEAN",
        ("https://www.sebi.gov.in/legal/circulars/dec-2018/physical-settlement-of-stock-derivatives_41482.html",
         "https://nsearchives.nseindia.com/content/circulars/INSP47293.pdf",
         "https://www.sebi.gov.in/legal/circulars/oct-2010/european-style-stock-options_6846.html"),
    ),
    IndiaExpiryRule(
        "india-stock-calendar-spread-expiry-no-offset-v1", "NFO", "STOCK",
        date(2026, 5, 5), "PHYSICAL", "EUROPEAN",
        ("https://www.sebi.gov.in/sebi_data/attachdocs/feb-2026/1770288869049.pdf",),
    ),
)


def rulebook_sha256() -> str:
    body = [
        {**asdict(row), "effective_from": row.effective_from.isoformat(),
         "source_refs": list(row.source_refs)}
        for row in RULES
    ]
    return _sha(body)


def rules_as_dicts() -> list[dict[str, object]]:
    """Return the reviewed effective-dated rules for evidence and audit reports."""
    return [
        {**asdict(row), "effective_from": row.effective_from.isoformat(),
         "source_refs": list(row.source_refs)}
        for row in RULES
    ]


def _sha(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _source(value: str, name: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise IndiaExpiryError(f"{name} must be a SHA-256 digest")


def account_state_sha256(account: OptionsPaperAccount) -> str:
    contracts = {
        key: {**value.as_dict(), "quantity_lots": account._lots.get(key, 0)}
        for key, value in sorted(account._contracts.items())
        if account._lots.get(key, 0)
    }
    body = {
        "account_id": account.account_id,
        "cash": str(account.cash),
        "contracts": contracts,
        "underlying_positions": account.underlying_positions,
        "underlying_marks": {key: str(value) for key, value in sorted(account._underlying_marks.items())},
    }
    return _sha(body)


def _rule_for(contract, when: date) -> IndiaExpiryRule | None:
    matches = [rule for rule in RULES if rule.venue == contract.market
               and rule.underlying_kind == contract.underlying_kind
               and rule.effective_from <= when]
    # Calendar-spread rule is a margin overlay, not a second settlement rule.
    matches = [rule for rule in matches if "calendar-spread" not in rule.rule_id]
    return max(matches, key=lambda item: item.effective_from) if len(matches) == 1 else None


def _prices_sha256(prices: Mapping[str, Decimal]) -> str:
    return _sha({key: str(Decimal(value)) for key, value in sorted(prices.items())})


def _readiness_body(report: ExpiryReadiness | Mapping[str, object]) -> dict[str, object]:
    raw = asdict(report) if isinstance(report, ExpiryReadiness) else dict(report)
    raw.pop("readiness_sha256", None)
    session = raw.get("session_date")
    if isinstance(session, date):
        raw["session_date"] = session.isoformat()
    return raw


def preflight_india_expiry(
    account: OptionsPaperAccount,
    *,
    settlement_prices: Mapping[str, Decimal],
    session_date: date,
    settlement_source_sha256: str,
    delivery_margins: Mapping[str, DeliveryMarginEvidence] | None = None,
) -> ExpiryReadiness:
    """Check effective rule, physical inventory, delivery cash and source-hashed margin.

    Any missing rule, settlement price, physical-delivery margin or deliverable shares
    blocks settlement. The margin input is supplied from an effective-dated exchange or
    broker evidence table; this module does not invent a current exchange rate.
    """
    _source(settlement_source_sha256, "settlement source")
    if type(session_date) is not date:
        raise IndiaExpiryError("session_date must be a date")
    prices = {key: Decimal(value) for key, value in settlement_prices.items()}
    if any(not value.is_finite() or value <= 0 for value in prices.values()):
        raise IndiaExpiryError("settlement prices must be positive and finite")
    margins = dict(delivery_margins or {})
    blockers: set[str] = set()
    cash_outflow = Decimal(0)
    total_flow = Decimal(0)
    delivery_margin = Decimal(0)
    shares: dict[str, int] = {}
    used_margin: dict[str, dict] = {}
    active = [key for key, lots in account._lots.items()
              if lots and account._contracts[key].expiry == session_date]
    if not active:
        blockers.add("NO_OPEN_EXPIRING_OPTIONS")

    for contract_id in active:
        contract, lots = account._contracts[contract_id], account._lots[contract_id]
        rule = _rule_for(contract, session_date)
        if rule is None:
            blockers.add(f"NO_EFFECTIVE_RULE:{contract_id}")
            continue
        if contract.settlement != rule.settlement or contract.exercise_style != rule.exercise_style:
            blockers.add(f"CONTRACT_RULE_MISMATCH:{contract_id}")
        if contract.underlying not in prices:
            blockers.add(f"MISSING_SETTLEMENT_PRICE:{contract.underlying}")
            continue
        spot = prices[contract.underlying]
        intrinsic = (max(spot - contract.strike, Decimal(0)) if contract.kind == "CALL"
                     else max(contract.strike - spot, Decimal(0)))
        if not intrinsic:
            continue
        signed_units = lots * contract.lot_size
        if rule.settlement == "CASH":
            flow = Decimal(signed_units) * intrinsic
        else:
            direction = 1 if contract.kind == "CALL" else -1
            share_delta = signed_units * direction
            shares[contract.underlying] = shares.get(contract.underlying, 0) + share_delta
            flow = -Decimal(share_delta) * contract.strike
            evidence = margins.get(contract_id)
            if evidence is None:
                blockers.add(f"MISSING_DELIVERY_MARGIN:{contract_id}")
            else:
                _source(evidence.source_sha256, "delivery margin source")
                if evidence.contract_id != contract_id or not evidence.source_id.strip():
                    blockers.add(f"INVALID_DELIVERY_MARGIN_IDENTITY:{contract_id}")
                elif (not isinstance(evidence.margin_per_lot, Decimal)
                      or not evidence.margin_per_lot.is_finite()
                      or evidence.margin_per_lot <= 0):
                    blockers.add(f"INVALID_DELIVERY_MARGIN:{contract_id}")
                else:
                    amount = evidence.margin_per_lot * abs(lots)
                    delivery_margin += amount
                    used_margin[contract_id] = {
                        "amount": str(amount), "source_id": evidence.source_id,
                        "source_sha256": evidence.source_sha256,
                    }
        total_flow += flow
        cash_outflow += max(-flow, Decimal(0))

    current_shares = account.underlying_positions
    for symbol, delta in shares.items():
        if delta < 0 and current_shares.get(symbol, 0) < abs(delta):
            blockers.add(f"INSUFFICIENT_DELIVERY_INVENTORY:{symbol}")
    if account.cash < cash_outflow + delivery_margin:
        blockers.add("INSUFFICIENT_EXPIRY_CASH_AND_DELIVERY_MARGIN")
    ending_cash = account.cash + total_flow
    if ending_cash < 0:
        blockers.add("NEGATIVE_CASH_AFTER_EXPIRY")

    margins_sha = _sha(used_margin)
    status = "BLOCKED" if blockers else "READY"
    price_hash = _prices_sha256(prices)
    account_hash = account_state_sha256(account)
    rules_hash = rulebook_sha256()
    cash_outflow_text = str(cash_outflow.quantize(Decimal("0.01")))
    delivery_margin_text = str(delivery_margin.quantize(Decimal("0.01")))
    ending_cash_text = str(ending_cash.quantize(Decimal("0.01")))
    physical_deltas = tuple(sorted(shares.items()))
    blocker_tuple = tuple(sorted(blockers))
    body: dict[str, object] = {
        "status": status,
        "session_date": session_date.isoformat(),
        "account_id": account.account_id,
        "settlement_source_sha256": settlement_source_sha256,
        "settlement_prices_sha256": price_hash,
        "account_state_sha256": account_hash,
        "rulebook_sha256": rules_hash,
        "delivery_margin_sha256": margins_sha,
        "cash_outflow_inr": cash_outflow_text,
        "delivery_margin_inr": delivery_margin_text,
        "ending_cash_inr": ending_cash_text,
        "physical_share_deltas": physical_deltas,
        "blockers": blocker_tuple,
    }
    digest = _sha(body)
    return ExpiryReadiness(
        status, session_date, account.account_id, settlement_source_sha256, price_hash,
        account_hash, rules_hash, margins_sha, cash_outflow_text, delivery_margin_text,
        ending_cash_text, physical_deltas, blocker_tuple, digest,
    )


def readiness_matches(account: OptionsPaperAccount, readiness: ExpiryReadiness | Mapping[str, object] | None,
                      *, settlement_prices: Mapping[str, Decimal], session_date: date,
                      settlement_source_sha256: str) -> bool:
    if readiness is None:
        return False
    raw = asdict(readiness) if isinstance(readiness, ExpiryReadiness) else dict(readiness)
    body = _readiness_body(raw)
    if _sha(body) != raw.get("readiness_sha256"):
        return False
    return (
        raw.get("status") == "READY"
        and raw.get("session_date") in {session_date, session_date.isoformat()}
        and raw.get("account_id") == account.account_id
        and raw.get("account_state_sha256") == account_state_sha256(account)
        and raw.get("settlement_prices_sha256") == _prices_sha256(settlement_prices)
        and raw.get("settlement_source_sha256") == settlement_source_sha256
        and raw.get("rulebook_sha256") == rulebook_sha256()
    )


def verify_stored_readiness(account: OptionsPaperAccount, row: Mapping[str, object]) -> bool:
    readiness = row.get("india_expiry_preflight")
    prices = row.get("settlement_prices")
    if not isinstance(readiness, dict) or not isinstance(prices, dict):
        return False
    body = _readiness_body(readiness)
    return (
        _sha(body) == readiness.get("readiness_sha256")
        and readiness.get("status") == "READY"
        and readiness.get("session_date") == row.get("session_date")
        and readiness.get("settlement_source_sha256") == row.get("source_sha256")
        and readiness.get("settlement_prices_sha256") == _prices_sha256(
            {key: Decimal(str(value)) for key, value in prices.items()}
        )
        and readiness.get("account_state_sha256") == account_state_sha256(account)
        and readiness.get("rulebook_sha256") == rulebook_sha256()
    )
