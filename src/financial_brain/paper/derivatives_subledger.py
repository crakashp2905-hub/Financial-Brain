"""Durable paper subledger for multi-leg option fills and expiry settlement.

It records observed paper fills and supplied expiry settlement evidence. It does
not route orders, attest market-data rights, or claim broker margin/settlement.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from financial_brain.portfolio.canonical_ledger import PortfolioEvent


class DerivativesLedgerError(ValueError):
    """Invalid or conflicting paper derivatives lifecycle evidence."""


def _decimal(name: str, value: Decimal | str | int | float, *, allow_zero: bool = False) -> Decimal:
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise DerivativesLedgerError(f"{name} must be decimal") from exc
    if not parsed.is_finite() or parsed < 0 or (parsed == 0 and not allow_zero):
        raise DerivativesLedgerError(f"{name} must be finite and positive")
    return parsed


def _aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DerivativesLedgerError(f"{name} must be timezone-aware")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


@dataclass(frozen=True, slots=True)
class OptionContract:
    contract_id: str
    underlying: str
    option_type: str
    strike: Decimal
    expiry: date
    lot_size: int
    settlement: str
    terms_sha256: str

    def __post_init__(self) -> None:
        if not all((self.contract_id.strip(), self.underlying.strip())):
            raise DerivativesLedgerError("contract and underlying identities are required")
        if self.option_type not in {"CE", "PE"} or self.settlement not in {"CASH", "PHYSICAL"}:
            raise DerivativesLedgerError("option type and settlement style must be explicit")
        object.__setattr__(self, "strike", _decimal("strike", self.strike))
        if type(self.lot_size) is not int or self.lot_size < 1:
            raise DerivativesLedgerError("lot size must be a positive integer")
        if len(self.terms_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.terms_sha256):
            raise DerivativesLedgerError("contract terms need a lowercase SHA-256 source hash")


@dataclass(frozen=True, slots=True)
class OptionLegOrder:
    leg_id: str
    contract: OptionContract
    side: str
    quantity: int

    def __post_init__(self) -> None:
        if not self.leg_id.strip() or self.side not in {"BUY", "SELL"}:
            raise DerivativesLedgerError("leg identity and BUY/SELL side are required")
        if type(self.quantity) is not int or self.quantity < 1:
            raise DerivativesLedgerError("requested option quantity must be a positive integer")
        if self.quantity % self.contract.lot_size:
            raise DerivativesLedgerError("requested option quantity must respect contract lot size")


class DerivativesSubledger:
    """SQLite append-only per-package event journal with replay-safe identities."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("""CREATE TABLE IF NOT EXISTS derivative_packages (
            package_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, structure TEXT NOT NULL,
            definition_json TEXT NOT NULL, definition_sha256 TEXT NOT NULL)""")
        self._con.execute("""CREATE TABLE IF NOT EXISTS derivative_events (
            package_id TEXT NOT NULL, sequence INTEGER NOT NULL, event_id TEXT NOT NULL UNIQUE,
            event_type TEXT NOT NULL, payload_json TEXT NOT NULL, previous_sha256 TEXT NOT NULL,
            event_sha256 TEXT NOT NULL, PRIMARY KEY(package_id, sequence))""")
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def create_package(self, *, package_id: str, account_id: str, structure: str,
                       legs: tuple[OptionLegOrder, ...], created_at: datetime) -> bool:
        _aware("created_at", created_at)
        if not package_id.strip() or not account_id.strip() or not legs:
            raise DerivativesLedgerError("package, account and at least one leg are required")
        if len({leg.leg_id for leg in legs}) != len(legs):
            raise DerivativesLedgerError("leg IDs must be unique")
        if len({leg.contract.contract_id for leg in legs}) != len(legs):
            raise DerivativesLedgerError("contract IDs must be unique within a package")
        if structure not in {"DEBIT_VERTICAL", "UNDEFINED_RISK"}:
            raise DerivativesLedgerError("structure must be DEBIT_VERTICAL or UNDEFINED_RISK")
        if structure == "DEBIT_VERTICAL":
            if len(legs) != 2 or {leg.side for leg in legs} != {"BUY", "SELL"}:
                raise DerivativesLedgerError("debit vertical requires one long and one short leg")
            first, second = (leg.contract for leg in legs)
            if (first.underlying, first.option_type, first.expiry, first.lot_size, first.settlement) != (
                    second.underlying, second.option_type, second.expiry, second.lot_size, second.settlement):
                raise DerivativesLedgerError("debit vertical contracts must share underlying, expiry and terms")
            long = next(leg.contract for leg in legs if leg.side == "BUY")
            short = next(leg.contract for leg in legs if leg.side == "SELL")
            oriented = long.strike < short.strike if long.option_type == "CE" else long.strike > short.strike
            if not oriented:
                raise DerivativesLedgerError("debit vertical strikes are inverted")
            if len({leg.quantity for leg in legs}) != 1:
                raise DerivativesLedgerError("defined-risk spread legs must request equal quantities")
        if any(leg.contract.expiry <= created_at.date() for leg in legs):
            raise DerivativesLedgerError("cannot create a paper option package on or after contract expiry")
        definition = {
            "package_id": package_id, "account_id": account_id, "structure": structure,
            "created_at": created_at.astimezone(timezone.utc).isoformat(),
            "legs": [{"leg_id": leg.leg_id, "side": leg.side, "quantity": leg.quantity,
                      "contract": {"contract_id": leg.contract.contract_id,
                                   "underlying": leg.contract.underlying,
                                   "option_type": leg.contract.option_type,
                                   "strike": str(leg.contract.strike),
                                   "expiry": leg.contract.expiry.isoformat(),
                                   "lot_size": leg.contract.lot_size,
                                   "settlement": leg.contract.settlement,
                                   "terms_sha256": leg.contract.terms_sha256}}
                     for leg in legs],
        }
        body = _canonical(definition)
        existing = self._con.execute("SELECT account_id,structure,definition_json FROM derivative_packages "
                                     "WHERE package_id=?", [package_id]).fetchone()
        if existing:
            if existing != (account_id, structure, body):
                raise DerivativesLedgerError("package ID conflicts with existing definition")
            return False
        self._con.execute("BEGIN IMMEDIATE")
        try:
            self._con.execute("INSERT INTO derivative_packages VALUES (?,?,?,?,?)",
                              [package_id, account_id, structure, body, _hash(body)])
            self._append_event(package_id, f"{package_id}:opened", "PACKAGE_OPENED",
                               {"created_at": definition["created_at"], "definition_sha256": _hash(body)})
            self._con.commit()
        except Exception:
            self._con.rollback()
            raise
        return True

    def _definition(self, package_id: str) -> tuple[str, dict[str, Any]]:
        row = self._con.execute("SELECT structure,definition_json,definition_sha256 FROM derivative_packages "
                                "WHERE package_id=?", [package_id]).fetchone()
        if row is None:
            raise DerivativesLedgerError("unknown derivative package")
        if _hash(row[1]) != row[2]:
            raise DerivativesLedgerError("package definition hash mismatch")
        return row[0], json.loads(row[1])

    def _append_event(self, package_id: str, event_id: str, event_type: str,
                      payload: dict[str, Any]) -> bool:
        payload_json = _canonical(payload)
        existing = self._con.execute("SELECT package_id,event_type,payload_json FROM derivative_events "
                                     "WHERE event_id=?", [event_id]).fetchone()
        if existing:
            if existing != (package_id, event_type, payload_json):
                raise DerivativesLedgerError("event ID conflicts with prior evidence")
            return False
        row = self._con.execute("SELECT sequence,event_sha256 FROM derivative_events WHERE package_id=? "
                                "ORDER BY sequence DESC LIMIT 1", [package_id]).fetchone()
        sequence, previous = (int(row[0]) + 1, row[1]) if row else (1, "0" * 64)
        event_body = _canonical({"package_id": package_id, "sequence": sequence,
                                  "event_id": event_id, "event_type": event_type,
                                  "payload": json.loads(payload_json), "previous_sha256": previous})
        self._con.execute("INSERT INTO derivative_events VALUES (?,?,?,?,?,?,?)",
                          [package_id, sequence, event_id, event_type, payload_json, previous, _hash(event_body)])
        return True

    def record_fill(self, *, package_id: str, event_id: str, leg_id: str, side: str,
                    quantity: int, price: Decimal, occurred_at: datetime,
                    quote_event_id: str, quote_observed_at: datetime,
                    quote_available_at: datetime,
                    quote_evidence_sha256: str) -> bool:
        _aware("occurred_at", occurred_at)
        _aware("quote_observed_at", quote_observed_at)
        _aware("quote_available_at", quote_available_at)
        fill_price = _decimal("fill price", price)
        if quote_observed_at > quote_available_at or quote_available_at > occurred_at:
            raise DerivativesLedgerError("fill quote must be observed and available by the observed fill time")
        if not quote_event_id.strip() or len(quote_evidence_sha256) != 64 or any(
                char not in "0123456789abcdef" for char in quote_evidence_sha256):
            raise DerivativesLedgerError("fill requires quote identity and source SHA-256")
        _, definition = self._definition(package_id)
        leg = next((row for row in definition["legs"] if row["leg_id"] == leg_id), None)
        if leg is None or side != leg["side"]:
            raise DerivativesLedgerError("fill leg or side differs from the frozen package definition")
        if type(quantity) is not int or quantity < 1 or quantity % leg["contract"]["lot_size"]:
            raise DerivativesLedgerError("fill quantity must be a positive multiple of the contract lot")
        if occurred_at.date() >= date.fromisoformat(leg["contract"]["expiry"]):
            raise DerivativesLedgerError("cannot paper-fill an expired option contract")
        created_at = datetime.fromisoformat(definition["created_at"])
        if quote_observed_at < created_at or occurred_at < created_at:
            raise DerivativesLedgerError("option fill evidence must follow the frozen package decision")
        payload = {"leg_id": leg_id, "side": side, "quantity": quantity,
                   "price": str(fill_price), "occurred_at": occurred_at.astimezone(timezone.utc).isoformat(),
                   "quote_event_id": quote_event_id,
                   "quote_observed_at": quote_observed_at.astimezone(timezone.utc).isoformat(),
                   "quote_available_at": quote_available_at.astimezone(timezone.utc).isoformat(),
                   "quote_evidence_sha256": quote_evidence_sha256}
        self._con.execute("BEGIN IMMEDIATE")
        try:
            existing = self._con.execute("SELECT package_id,event_type,payload_json FROM derivative_events "
                                         "WHERE event_id=?", [event_id]).fetchone()
            if existing:
                if existing != (package_id, "LEG_FILL", _canonical(payload)):
                    raise DerivativesLedgerError("event ID conflicts with prior evidence")
                self._con.commit()
                return False
            if self._has_settlement(package_id, leg["contract"]["contract_id"]):
                raise DerivativesLedgerError("cannot add fills after expiry settlement")
            prior = sum(int(row[0]) for row in self._con.execute(
                "SELECT json_extract(payload_json,'$.quantity') FROM derivative_events "
                "WHERE package_id=? AND event_type='LEG_FILL' AND json_extract(payload_json,'$.leg_id')=?",
                [package_id, leg_id]).fetchall())
            if prior + quantity > leg["quantity"]:
                raise DerivativesLedgerError("leg fills exceed its requested quantity")
            created = self._append_event(package_id, event_id, "LEG_FILL", payload)
            self._con.commit()
            return created
        except Exception:
            self._con.rollback()
            raise

    def _has_settlement(self, package_id: str, contract_id: str | None = None) -> bool:
        query = ("SELECT 1 FROM derivative_events WHERE package_id=? AND event_type='EXPIRY_SETTLEMENT' "
                 + ("AND json_extract(payload_json,'$.contract_id')=? " if contract_id is not None else ""))
        params = [package_id, contract_id] if contract_id is not None else [package_id]
        return self._con.execute(query, params).fetchone() is not None

    def snapshot(self, package_id: str) -> dict[str, Any]:
        structure, definition = self._definition(package_id)
        events = self._con.execute("SELECT sequence,event_id,event_type,payload_json,previous_sha256,event_sha256 "
                                   "FROM derivative_events WHERE package_id=? ORDER BY sequence", [package_id]).fetchall()
        previous = "0" * 64
        fills: dict[str, int] = {leg["leg_id"]: 0 for leg in definition["legs"]}
        premium_cash = Decimal(0)
        leg_premium_cash: dict[str, Decimal] = {leg["leg_id"]: Decimal(0) for leg in definition["legs"]}
        for expected_sequence, row in enumerate(events, 1):
            sequence, event_id, event_type, payload_text, prev_hash, event_hash = row
            payload = json.loads(payload_text)
            body = _canonical({"package_id": package_id, "sequence": sequence,
                               "event_id": event_id, "event_type": event_type,
                               "payload": payload, "previous_sha256": previous})
            if sequence != expected_sequence or prev_hash != previous or event_hash != _hash(body):
                raise DerivativesLedgerError("derivative event hash chain is invalid")
            previous = event_hash
            if event_type == "LEG_FILL":
                leg = next(item for item in definition["legs"] if item["leg_id"] == payload["leg_id"])
                fills[leg["leg_id"]] += payload["quantity"]
                sign = Decimal(-1) if leg["side"] == "BUY" else Decimal(1)
                leg_premium = sign * Decimal(payload["price"]) * payload["quantity"]
                premium_cash += leg_premium
                leg_premium_cash[leg["leg_id"]] += leg_premium
        ratios = {Decimal(fills[leg["leg_id"]]) / Decimal(leg["quantity"])
                  for leg in definition["legs"]}
        all_filled = all(fills[leg["leg_id"]] == leg["quantity"] for leg in definition["legs"])
        balanced = len(ratios) == 1 and next(iter(ratios), Decimal(0)) > 0
        defined_risk = structure == "DEBIT_VERTICAL" and balanced and premium_cash < 0
        settlement_events = [json.loads(row[3]) for row in events if row[2] == "EXPIRY_SETTLEMENT"]
        settled_contracts = {row["contract_id"] for row in settlement_events}
        filled_contracts = {leg["contract"]["contract_id"] for leg in definition["legs"]
                           if fills[leg["leg_id"]] > 0}
        settled = bool(filled_contracts) and filled_contracts <= settled_contracts
        status = ("SETTLED" if settled else "PARTIALLY_SETTLED" if settlement_events else
                  "UNFILLED" if not any(fills.values()) else
                  "UNHEDGED_PARTIAL" if not balanced else
                  "OPEN_DEFINED_RISK" if all_filled and defined_risk else
                  "BALANCED_PARTIAL" if defined_risk else "OPEN_UNDEFINED_RISK")
        return {"package_id": package_id, "structure": structure, "status": status,
                "leg_filled_quantities": fills, "premium_cash_flow_inr": premium_cash,
                "leg_premium_cash_flows_inr": leg_premium_cash,
                "defined_risk": defined_risk and not settled,
                "capital_deployment_allowed": defined_risk and not settled,
                "settlement_status": "SETTLED" if settled else "PARTIALLY_SETTLED" if settlement_events else "NOT_SETTLED",
                "settlements": settlement_events, "event_count": len(events),
                "event_chain_sha256": previous}

    def to_portfolio_events(self, package_id: str) -> list[PortfolioEvent]:
        """Project verified option fills and settlements into canonical portfolio events.

        Cash-settled expiry closes the option at intrinsic value. Physical expiry closes the
        option at zero, then records assignment/delivery at strike and a source-time
        underlying mark. The source hash is the verified derivative event-chain digest.
        """
        from financial_brain.portfolio.canonical_ledger import (
            AssetClass, EventKind, PortfolioEvent,
        )

        self.snapshot(package_id)  # verifies package definition and every hash-chain link
        _, definition = self._definition(package_id)
        rows = self._con.execute(
            "SELECT event_id,event_type,payload_json,event_sha256 FROM derivative_events "
            "WHERE package_id=? ORDER BY sequence", [package_id]).fetchall()
        contracts = {leg["leg_id"]: leg for leg in definition["legs"]}
        source_system = "paper.derivatives_subledger"
        account_id = definition["account_id"]
        events: list[PortfolioEvent] = []
        for event_id, event_type, payload_text, source_hash in rows:
            payload = json.loads(payload_text)
            if event_type == "LEG_FILL":
                leg = contracts[payload["leg_id"]]
                events.append(PortfolioEvent(
                    account_id=account_id, sleeve_id=package_id, source_system=source_system,
                    source_event_id=event_id, source_sha256=source_hash, event_kind=EventKind.FILL,
                    occurred_at=payload["occurred_at"], currency="INR", asset_class=AssetClass.OPTION,
                    instrument_id=leg["contract"]["contract_id"], side=payload["side"],
                    quantity=payload["quantity"], price=payload["price"],
                    evidence_class="LOCAL_PAPER", metadata={
                        "package_id": package_id, "derivative_event_id": event_id,
                        "derivative_event_sha256": source_hash, "leg_id": payload["leg_id"],
                        "quote_event_id": payload["quote_event_id"],
                        "quote_observed_at": payload["quote_observed_at"],
                        "quote_available_at": payload["quote_available_at"],
                        "quote_evidence_sha256": payload["quote_evidence_sha256"],
                    }))
                continue
            if event_type != "EXPIRY_SETTLEMENT":
                continue
            settlement_at = payload["settlement_at"]
            for settled_leg in payload["legs"]:
                leg = contracts[settled_leg["leg_id"]]
                contract = leg["contract"]
                close_side = "SELL" if leg["side"] == "BUY" else "BUY"
                expiry_price = (Decimal(settled_leg["intrinsic_per_unit"])
                                if contract["settlement"] == "CASH" else Decimal(0))
                close_id = f"{event_id}:{contract['contract_id']}:option-expiry"
                events.append(PortfolioEvent(
                    account_id=account_id, sleeve_id=package_id, source_system=source_system,
                    source_event_id=close_id, source_sha256=source_hash,
                    event_kind=EventKind.EXPIRY_SETTLEMENT, occurred_at=settlement_at,
                    currency="INR", asset_class=AssetClass.OPTION,
                    instrument_id=contract["contract_id"], side=close_side,
                    quantity=settled_leg["filled_quantity"], price=expiry_price,
                    evidence_class="LOCAL_PAPER", metadata={
                        "package_id": package_id, "settlement_event_id": event_id,
                        "settlement_event_sha256": source_hash,
                        "settlement_source_id": payload["source_id"],
                        "settlement_source_sha256": payload["source_sha256"],
                        "expiry_calendar_sha256": payload["expiry_calendar_sha256"],
                        "settlement_style": contract["settlement"],
                        "intrinsic_per_unit": settled_leg["intrinsic_per_unit"],
                    }))
                underlying_units = int(settled_leg["underlying_units"])
                if contract["settlement"] == "PHYSICAL" and underlying_units:
                    underlying_side = "BUY" if underlying_units > 0 else "SELL"
                    events.append(PortfolioEvent(
                        account_id=account_id, sleeve_id=package_id, source_system=source_system,
                        source_event_id=f"{event_id}:{contract['contract_id']}:underlying-delivery",
                        source_sha256=source_hash, event_kind=EventKind.FILL,
                        occurred_at=settlement_at, currency="INR", asset_class=AssetClass.EQUITY,
                        instrument_id=contract["underlying"], side=underlying_side,
                        quantity=abs(underlying_units), price=contract["strike"],
                        evidence_class="LOCAL_PAPER", metadata={
                            "package_id": package_id, "settlement_event_id": event_id,
                            "settlement_event_sha256": source_hash,
                            "settlement_source_id": payload["source_id"],
                            "settlement_source_sha256": payload["source_sha256"],
                            "delivery_contract_id": contract["contract_id"],
                        }))
                    events.append(PortfolioEvent(
                        account_id=account_id, sleeve_id=package_id, source_system=source_system,
                        source_event_id=f"{event_id}:{contract['contract_id']}:underlying-mark",
                        source_sha256=source_hash, event_kind=EventKind.MARK,
                        occurred_at=settlement_at, currency="INR", asset_class=AssetClass.EQUITY,
                        instrument_id=contract["underlying"], price=payload["underlying_price"],
                        evidence_class="LOCAL_PAPER", metadata={
                            "package_id": package_id, "settlement_event_id": event_id,
                            "settlement_event_sha256": source_hash,
                            "settlement_source_id": payload["source_id"],
                            "settlement_source_sha256": payload["source_sha256"],
                        }))
        return events

    def settle_expiry(self, *, package_id: str, contract_id: str, event_id: str, settlement_date: date,
                      expiry_cutoff_at: datetime, expiry_calendar_sha256: str,
                      settlement_at: datetime, published_at: datetime, underlying_price: Decimal,
                      source_id: str, source_sha256: str) -> bool:
        _aware("settlement_at", settlement_at)
        _aware("published_at", published_at)
        _aware("expiry_cutoff_at", expiry_cutoff_at)
        spot = _decimal("underlying settlement price", underlying_price)
        if not source_id.strip() or len(source_sha256) != 64 or any(
                char not in "0123456789abcdef" for char in source_sha256):
            raise DerivativesLedgerError("expiry settlement requires source identity and SHA-256 evidence")
        _, definition = self._definition(package_id)
        leg = next((row for row in definition["legs"] if row["contract"]["contract_id"] == contract_id), None)
        if leg is None:
            raise DerivativesLedgerError("unknown contract in derivative package")
        if settlement_date < date.fromisoformat(leg["contract"]["expiry"]):
            raise DerivativesLedgerError("settlement date precedes contract expiry")
        if expiry_cutoff_at.date() != settlement_date:
            raise DerivativesLedgerError("exchange expiry cutoff date must match the settlement date")
        if (len(expiry_calendar_sha256) != 64 or any(c not in "0123456789abcdef" for c in expiry_calendar_sha256)):
            raise DerivativesLedgerError("expiry cutoff requires a source-hashed exchange calendar")
        if published_at < expiry_cutoff_at or settlement_at < published_at:
            raise DerivativesLedgerError("settlement must follow the source-hashed expiry cutoff and publication")
        self._con.execute("BEGIN IMMEDIATE")
        try:
            snapshot = self.snapshot(package_id)
            quantity = snapshot["leg_filled_quantities"][leg["leg_id"]]
            if quantity == 0:
                raise DerivativesLedgerError("cannot settle a contract with no filled quantity")
            if self._has_settlement(package_id, contract_id):
                prior = self._con.execute("SELECT event_id FROM derivative_events WHERE package_id=? "
                                          "AND event_type='EXPIRY_SETTLEMENT' "
                                          "AND json_extract(payload_json,'$.contract_id')=?",
                                          [package_id, contract_id]).fetchone()
                if not prior or prior[0] != event_id:
                    raise DerivativesLedgerError("contract expiry settlement is already recorded")
            contract = leg["contract"]
            intrinsic = max(Decimal(0), spot - Decimal(contract["strike"])) if contract["option_type"] == "CE" else \
                max(Decimal(0), Decimal(contract["strike"]) - spot)
            itm_quantity = quantity if intrinsic > 0 else 0
            signed_intrinsic = intrinsic * quantity * (1 if leg["side"] == "BUY" else -1)
            if contract["settlement"] == "CASH":
                cash, units = signed_intrinsic, 0
            elif contract["option_type"] == "CE":
                cash = (-1 if leg["side"] == "BUY" else 1) * Decimal(contract["strike"]) * itm_quantity
                units = (1 if leg["side"] == "BUY" else -1) * itm_quantity
            else:
                cash = (1 if leg["side"] == "BUY" else -1) * Decimal(contract["strike"]) * itm_quantity
                units = (-1 if leg["side"] == "BUY" else 1) * itm_quantity
            settlement_value = cash + Decimal(units) * spot
            premium = Decimal(snapshot["leg_premium_cash_flows_inr"][leg["leg_id"]])
            settlement = {"leg_id": leg["leg_id"], "contract_id": contract_id,
                          "filled_quantity": quantity, "intrinsic_per_unit": str(intrinsic),
                          "cash_flow_inr": str(cash), "underlying_units": units,
                          "settlement_value_inr": str(settlement_value),
                          "premium_cash_flow_inr": str(premium),
                          "gross_pnl_before_charges_inr": str(settlement_value + premium),
                          "settlement": contract["settlement"]}
            payload = {"settlement_date": settlement_date.isoformat(),
                       "expiry_cutoff_at": expiry_cutoff_at.astimezone(timezone.utc).isoformat(),
                       "expiry_calendar_sha256": expiry_calendar_sha256,
                       "settlement_at": settlement_at.astimezone(timezone.utc).isoformat(),
                       "published_at": published_at.astimezone(timezone.utc).isoformat(),
                       "underlying_price": str(spot), "source_id": source_id,
                       "source_sha256": source_sha256, "contract_id": contract_id,
                       "legs": [settlement], "settlement_cash_flow_inr": str(cash),
                       "status": "PAPER_SETTLEMENT_EVIDENCE_NOT_EXCHANGE_CONFIRMATION"}
            created = self._append_event(package_id, event_id, "EXPIRY_SETTLEMENT", payload)
            self._con.commit()
            return created
        except Exception:
            self._con.rollback()
            raise


__all__ = ["DerivativesLedgerError", "DerivativesSubledger", "OptionContract", "OptionLegOrder"]
