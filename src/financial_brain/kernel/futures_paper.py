"""Durable paper accounting for one futures contract at a time.

Contract tick, multiplier, lot and margin inputs are owner/provider supplied and source-hashed; no Indian exchange
values are hardcoded. Variation margin is cash-settled daily. This simulator has no broker path and is not a margin
or execution-cost quote.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.kernel.ledger import CREDIT, DEBIT, DoubleEntryLedger, Entry, Transaction
from financial_brain.kernel.units import Money


class FuturesPaperError(ValueError):
    """Invalid futures paper contract or lifecycle event."""


@dataclass(frozen=True)
class FuturesContract:
    contract_id: str
    symbol: str
    expiry: date
    tick: Decimal
    lot_size: int
    multiplier: Decimal
    initial_margin_per_lot: Decimal
    maintenance_margin_per_lot: Decimal
    source_id: str
    source_sha256: str

    def __post_init__(self) -> None:
        if not self.contract_id.strip() or not self.symbol.strip() or not self.source_id.strip():
            raise FuturesPaperError("contract identity and source id are required")
        if isinstance(self.expiry, type(date.today())) is False:
            raise FuturesPaperError("contract expiry must be a date")
        for name, value in (("tick", self.tick), ("multiplier", self.multiplier),
                            ("initial margin", self.initial_margin_per_lot),
                            ("maintenance margin", self.maintenance_margin_per_lot)):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise FuturesPaperError(f"contract {name} must be a positive finite Decimal")
        if type(self.lot_size) is not int or self.lot_size <= 0:
            raise FuturesPaperError("contract lot_size must be a positive integer")
        if self.maintenance_margin_per_lot > self.initial_margin_per_lot:
            raise FuturesPaperError("maintenance margin cannot exceed initial margin")
        if not re.fullmatch(r"[0-9a-f]{64}", self.source_sha256):
            raise FuturesPaperError("contract source SHA-256 is required")

    def as_dict(self) -> dict:
        return {
            "contract_id": self.contract_id,
            "symbol": self.symbol,
            "expiry": self.expiry.isoformat(),
            "tick": str(self.tick),
            "lot_size": self.lot_size,
            "multiplier": str(self.multiplier),
            "initial_margin_per_lot": str(self.initial_margin_per_lot),
            "maintenance_margin_per_lot": str(self.maintenance_margin_per_lot),
            "source_id": self.source_id,
            "source_sha256": self.source_sha256,
        }


class FuturesPaperAccount:
    """Append-only single-currency future paper account with variation cash and sourced contract terms."""

    def __init__(self, path: Path, *, account_id: str, initial_cash: Decimal,
                 currency: str = "INR") -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", account_id or ""):
            raise FuturesPaperError("account_id must be a stable identifier")
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise FuturesPaperError("currency must be a three-letter uppercase code")
        cash = Decimal(initial_cash)
        if not cash.is_finite() or cash <= 0:
            raise FuturesPaperError("initial_cash must be positive and finite")
        self.path, self.account_id, self.currency = Path(path), account_id, currency
        self.journal = DurableJournal(self.path)
        rows = self.journal.records()
        opening = {"t": "FUTURES_ACCOUNT_OPEN", "account_id": account_id,
                   "initial_cash": str(cash), "currency": currency}
        if not rows:
            self.journal.append(opening)
        elif rows[0] != opening:
            raise FuturesPaperError("existing futures journal opening does not match account configuration")
        self._rebuild()

    @property
    def cash(self) -> Decimal:
        return self.ledger.balance(f"cash:{self.account_id}", self.currency)

    @property
    def position(self) -> int:
        return sum(qty for qty, _ in self._lots)

    @property
    def active_contract(self) -> FuturesContract | None:
        return self.contract

    @property
    def variation_pnl(self) -> Decimal:
        return -self.ledger.balance(f"variation_pnl:{self.account_id}", self.currency)

    @property
    def initial_margin(self) -> Decimal:
        if self.contract is None or not self.position:
            return Decimal(0)
        lots = abs(self.position) // self.contract.lot_size
        return self.contract.initial_margin_per_lot * lots

    @property
    def maintenance_margin(self) -> Decimal:
        if self.contract is None or not self.position:
            return Decimal(0)
        lots = abs(self.position) // self.contract.lot_size
        return self.contract.maintenance_margin_per_lot * lots

    @property
    def available_cash(self) -> Decimal:
        return self.cash - self.initial_margin

    @property
    def margin_call(self) -> bool:
        return bool(self.position) and self.cash < self.maintenance_margin

    def trade(self, event_id: str, contract: FuturesContract, *, quantity: int, price: Decimal,
              session_date: date, source_sha256: str) -> None:
        """Trade signed contracts (positive buys, negative sells); closing variation settles immediately."""
        price = Decimal(price)
        self._validate_event_id(event_id)
        self._validate_source(source_sha256)
        self._validate_price(contract, price)
        self._validate_quantity(contract, quantity)
        if session_date > contract.expiry:
            raise FuturesPaperError("expired contract cannot be traded")
        if self._last_event_date is not None and session_date < self._last_event_date:
            raise FuturesPaperError("futures events must be recorded in effective-date order")
        if self.position and self.contract != contract:
            raise FuturesPaperError("close or roll the active contract before trading another expiry")
        row = self._event_row("FUTURES_TRADE", event_id, {
            "contract": contract.as_dict(), "quantity": quantity, "price": str(price),
            "session_date": session_date.isoformat(), "source_sha256": source_sha256,
        })
        self._commit(row)

    def settle(self, event_id: str, *, settlement_price: Decimal, session_date: date,
               source_sha256: str) -> Decimal:
        """Post daily variation margin and reset open lots to the sourced settlement price."""
        price = Decimal(settlement_price)
        self._validate_event_id(event_id)
        self._validate_source(source_sha256)
        if self.contract is None or not self.position:
            raise FuturesPaperError("no open futures position to settle")
        self._validate_price(self.contract, price)
        if session_date > self.contract.expiry:
            raise FuturesPaperError("daily settlement cannot occur after expiry")
        if self._last_event_date is not None and session_date < self._last_event_date:
            raise FuturesPaperError("futures events must be recorded in effective-date order")
        row = self._event_row("FUTURES_SETTLEMENT", event_id, {
            "contract_id": self.contract.contract_id,
            "settlement_price": str(price), "session_date": session_date.isoformat(),
            "source_sha256": source_sha256,
        })
        self._commit(row)
        return self.variation_pnl

    def roll(self, event_id: str, new_contract: FuturesContract, *, old_exit_price: Decimal,
             new_entry_price: Decimal, session_date: date,
             old_price_source_sha256: str, new_price_source_sha256: str) -> None:
        """Close the old contract and open the same signed quantity in a later expiry atomically."""
        if self.contract is None or not self.position:
            raise FuturesPaperError("no open futures position to roll")
        old_price, new_price = Decimal(old_exit_price), Decimal(new_entry_price)
        self._validate_event_id(event_id)
        self._validate_source(old_price_source_sha256)
        self._validate_source(new_price_source_sha256)
        self._validate_price(self.contract, old_price)
        self._validate_price(new_contract, new_price)
        if new_contract.symbol != self.contract.symbol or new_contract.expiry <= self.contract.expiry:
            raise FuturesPaperError("roll must move the same root symbol to a later expiry")
        if session_date > self.contract.expiry:
            raise FuturesPaperError("old contract cannot be rolled after expiry")
        if self._last_event_date is not None and session_date < self._last_event_date:
            raise FuturesPaperError("futures events must be recorded in effective-date order")
        row = self._event_row("FUTURES_ROLL", event_id, {
            "old_contract": self.contract.as_dict(), "new_contract": new_contract.as_dict(),
            "old_exit_price": str(old_price), "new_entry_price": str(new_price),
            "session_date": session_date.isoformat(),
            "old_price_source_sha256": old_price_source_sha256,
            "new_price_source_sha256": new_price_source_sha256,
        })
        self._commit(row)

    def expire(self, event_id: str, *, final_settlement_price: Decimal, session_date: date,
               source_sha256: str) -> None:
        """Post final variation and close an open position on its contract expiry date."""
        price = Decimal(final_settlement_price)
        self._validate_event_id(event_id)
        self._validate_source(source_sha256)
        if self.contract is None or not self.position:
            raise FuturesPaperError("no open futures position to expire")
        self._validate_price(self.contract, price)
        if session_date != self.contract.expiry:
            raise FuturesPaperError("expiry settlement date must match the sourced contract expiry")
        if self._last_event_date is not None and session_date < self._last_event_date:
            raise FuturesPaperError("futures events must be recorded in effective-date order")
        row = self._event_row("FUTURES_EXPIRY", event_id, {
            "contract_id": self.contract.contract_id,
            "final_settlement_price": str(price), "session_date": session_date.isoformat(),
            "source_sha256": source_sha256,
        })
        self._commit(row)

    def _rebuild(self) -> None:
        self.ledger = DoubleEntryLedger()
        rows = self.journal.records()
        opening = rows[0]
        self.ledger.post(Transaction("futures-opening", (
            Entry(f"cash:{self.account_id}", DEBIT, Money.of(opening["initial_cash"], self.currency)),
            Entry(f"capital:{self.account_id}", CREDIT, Money.of(opening["initial_cash"], self.currency)),
        )))
        self.contract: FuturesContract | None = None
        self._lots: list[tuple[int, Decimal]] = []
        self._last_event_date: date | None = None
        self._event_requests: dict[str, str] = {}
        for row in rows[1:]:
            if row.get("t") == "FUTURES_TRADE":
                self._apply_trade(row)
            elif row.get("t") == "FUTURES_SETTLEMENT":
                self._apply_settlement(row, expire=False)
            elif row.get("t") == "FUTURES_ROLL":
                self._apply_roll(row)
            elif row.get("t") == "FUTURES_EXPIRY":
                self._apply_settlement(row, expire=True)
            else:
                raise FuturesPaperError(f"unknown futures journal event: {row.get('t')}")
        if not self.ledger.trial_balance_ok():
            raise FuturesPaperError("futures double-entry ledger does not balance")

    def _apply_trade(self, row: dict) -> None:
        event_id, request_sha = row["event_id"], row["request_sha256"]
        self._check_request(event_id, request_sha)
        contract = _contract_from_dict(row["contract"])
        qty, price = int(row["quantity"]), Decimal(row["price"])
        day = date.fromisoformat(row["session_date"])
        self._validate_price(contract, price)
        self._validate_quantity(contract, qty)
        if day > contract.expiry or self._last_event_date is not None and day < self._last_event_date:
            raise FuturesPaperError("durable trade date is outside the contract lifecycle")
        if self.position and self.contract != contract:
            raise FuturesPaperError("durable futures trade changes contract without a roll")
        if not self.position:
            self.contract = contract
        pnl, lots = _trade_lots(self._lots, qty, price, contract)
        self._post_variation(event_id, pnl)
        self._lots = lots
        if not self.position:
            self.contract = None
        elif self.cash < self.initial_margin:
            raise FuturesPaperError("trade would leave insufficient initial margin")
        self._last_event_date = day
        self._event_requests[event_id] = request_sha

    def _apply_settlement(self, row: dict, *, expire: bool) -> None:
        event_id, request_sha = row["event_id"], row["request_sha256"]
        self._check_request(event_id, request_sha)
        if self.contract is None or self.contract.contract_id != row["contract_id"] or not self.position:
            raise FuturesPaperError("settlement does not match an open contract")
        day = date.fromisoformat(row["session_date"])
        price = Decimal(row["final_settlement_price"] if expire else row["settlement_price"])
        self._validate_price(self.contract, price)
        if (day < (self._last_event_date or day) or day > self.contract.expiry
                or expire and day != self.contract.expiry):
            raise FuturesPaperError("settlement date violates the contract lifecycle")
        pnl = sum((Decimal(qty) * self.contract.multiplier * (price - reference)
                   for qty, reference in self._lots), Decimal(0))
        self._post_variation(event_id, pnl)
        self._lots = [] if expire else [(qty, price) for qty, _ in self._lots]
        if expire:
            self.contract = None
        self._last_event_date = day
        self._event_requests[event_id] = request_sha

    def _apply_roll(self, row: dict) -> None:
        event_id, request_sha = row["event_id"], row["request_sha256"]
        self._check_request(event_id, request_sha)
        old = _contract_from_dict(row["old_contract"])
        new = _contract_from_dict(row["new_contract"])
        day = date.fromisoformat(row["session_date"])
        old_price, new_price = Decimal(row["old_exit_price"]), Decimal(row["new_entry_price"])
        if self.contract != old or not self.position:
            raise FuturesPaperError("roll old contract does not match the open position")
        if new.symbol != old.symbol or new.expiry <= old.expiry or day > old.expiry:
            raise FuturesPaperError("roll contracts or date are invalid")
        if self._last_event_date is not None and day < self._last_event_date:
            raise FuturesPaperError("roll date precedes prior futures event")
        self._validate_price(old, old_price)
        self._validate_price(new, new_price)
        signed_qty = self.position
        if abs(signed_qty) % new.lot_size:
            raise FuturesPaperError("open quantity is not a whole lot in the new contract")
        pnl = sum((Decimal(qty) * old.multiplier * (old_price - reference)
                   for qty, reference in self._lots), Decimal(0))
        self._post_variation(event_id, pnl)
        self.contract = new
        self._lots = [(signed_qty, new_price)]
        if self.cash < self.initial_margin:
            raise FuturesPaperError("roll would leave insufficient initial margin on the new contract")
        self._last_event_date = day
        self._event_requests[event_id] = request_sha

    def _post_variation(self, event_id: str, amount: Decimal) -> None:
        amount = _paise(amount)
        if amount > 0:
            entries = (Entry(f"cash:{self.account_id}", DEBIT, Money.of(amount, self.currency)),
                       Entry(f"variation_pnl:{self.account_id}", CREDIT, Money.of(amount, self.currency)))
        elif amount < 0:
            magnitude = -amount
            entries = (Entry(f"variation_pnl:{self.account_id}", DEBIT, Money.of(magnitude, self.currency)),
                       Entry(f"cash:{self.account_id}", CREDIT, Money.of(magnitude, self.currency)))
        else:
            return
        self.ledger.post(Transaction(f"variation:{event_id}", entries))

    def _commit(self, row: dict) -> None:
        existing = next((record for record in self.journal.records()
                         if record.get("event_id") == row["event_id"]), None)
        if existing is not None:
            if existing.get("request_sha256") != row["request_sha256"]:
                raise FuturesPaperError("event id reused with different futures request")
            return
        preview = self.path.with_name(f"{self.path.name}.{uuid4().hex}.preview")
        preview.write_bytes(self.path.read_bytes())
        try:
            candidate = DurableJournal(preview)
            candidate.append(row)
            view = object.__new__(FuturesPaperAccount)
            view.path, view.account_id, view.currency, view.journal = self.path, self.account_id, self.currency, candidate
            view._rebuild()
            self.journal.append(row)
            self._rebuild()
        finally:
            preview.unlink(missing_ok=True)

    @staticmethod
    def _event_row(kind: str, event_id: str, request: dict) -> dict:
        payload = {"event_type": kind, "event_id": event_id, **request}
        request_sha = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                               allow_nan=False).encode("utf-8")).hexdigest()
        return {"t": kind, **payload, "request_sha256": request_sha, "physical_orders": False}

    def _check_request(self, event_id: str, request_sha: str) -> None:
        prior = self._event_requests.get(event_id)
        if prior is not None and prior != request_sha:
            raise FuturesPaperError("futures event ID is bound to conflicting request content")

    @staticmethod
    def _validate_event_id(event_id: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", event_id or ""):
            raise FuturesPaperError("event id must be stable")

    @staticmethod
    def _validate_source(source_sha256: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", source_sha256 or ""):
            raise FuturesPaperError("source SHA-256 is required")

    @staticmethod
    def _validate_quantity(contract: FuturesContract, quantity: int) -> None:
        if type(quantity) is not int or quantity == 0 or abs(quantity) % contract.lot_size:
            raise FuturesPaperError("futures quantity must be a nonzero signed lot-size multiple")

    @staticmethod
    def _validate_price(contract: FuturesContract, price: Decimal) -> None:
        if not price.is_finite() or price <= 0 or price % contract.tick != 0:
            raise FuturesPaperError("futures price must be positive, finite and tick-aligned")


def _trade_lots(lots: list[tuple[int, Decimal]], quantity: int, price: Decimal,
                contract: FuturesContract) -> tuple[Decimal, list[tuple[int, Decimal]]]:
    active = list(lots)
    delta = quantity
    pnl = Decimal(0)
    while delta and active and (delta > 0) != (active[0][0] > 0):
        lot_qty, reference = active[0]
        closed = min(abs(delta), abs(lot_qty))
        sign = Decimal(1) if lot_qty > 0 else Decimal(-1)
        pnl += sign * closed * contract.multiplier * (price - reference)
        remainder_lot = lot_qty - int(sign * closed)
        delta += int(sign * closed)
        if remainder_lot:
            active[0] = (remainder_lot, reference)
        else:
            active.pop(0)
    if delta:
        active.append((delta, price))
    return pnl, active


def _contract_from_dict(value: dict) -> FuturesContract:
    return FuturesContract(
        contract_id=value["contract_id"], symbol=value["symbol"], expiry=date.fromisoformat(value["expiry"]),
        tick=Decimal(value["tick"]), lot_size=int(value["lot_size"]),
        multiplier=Decimal(value["multiplier"]), initial_margin_per_lot=Decimal(value["initial_margin_per_lot"]),
        maintenance_margin_per_lot=Decimal(value["maintenance_margin_per_lot"]),
        source_id=value["source_id"], source_sha256=value["source_sha256"],
    )


def _paise(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise FuturesPaperError("variation cash must be finite")
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
