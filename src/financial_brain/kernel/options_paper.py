"""Durable paper options premium, spread-risk reserve, and expiry settlement.

All contract and naked-margin terms are supplied with source hashes. The computed margin is a
conservative paper reserve, not an exchange/broker requirement or execution quote.
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


class OptionsPaperError(ValueError):
    """Invalid option paper lifecycle event."""


@dataclass(frozen=True)
class OptionContract:
    contract_id: str
    underlying: str
    kind: str
    strike: Decimal
    expiry: date
    lot_size: int
    settlement: str
    short_margin_per_lot: Decimal
    source_id: str
    source_sha256: str
    market: str = "NFO"
    underlying_kind: str = "STOCK"
    exercise_style: str = "EUROPEAN"

    def __post_init__(self) -> None:
        if not self.contract_id.strip() or not self.underlying.strip() or not self.source_id.strip():
            raise OptionsPaperError("contract identity and source id are required")
        if self.kind not in {"CALL", "PUT"} or self.settlement not in {"CASH", "PHYSICAL"}:
            raise OptionsPaperError("kind must be CALL/PUT and settlement must be CASH/PHYSICAL")
        if self.market not in {"NFO", "BFO"} or self.underlying_kind not in {"INDEX", "STOCK"}:
            raise OptionsPaperError("market must be NFO/BFO and underlying_kind INDEX/STOCK")
        if self.exercise_style not in {"EUROPEAN", "AMERICAN"}:
            raise OptionsPaperError("exercise_style must be EUROPEAN or AMERICAN")
        if not isinstance(self.expiry, date):
            raise OptionsPaperError("contract expiry must be a date")
        for name, value in (("strike", self.strike), ("short margin", self.short_margin_per_lot)):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise OptionsPaperError(f"contract {name} must be a positive finite Decimal")
        if type(self.lot_size) is not int or self.lot_size <= 0:
            raise OptionsPaperError("lot_size must be a positive integer")
        if not re.fullmatch(r"[0-9a-f]{64}", self.source_sha256):
            raise OptionsPaperError("contract source SHA-256 is required")

    def as_dict(self) -> dict:
        return {
            "contract_id": self.contract_id, "underlying": self.underlying, "kind": self.kind,
            "strike": str(self.strike), "expiry": self.expiry.isoformat(), "lot_size": self.lot_size,
            "settlement": self.settlement, "short_margin_per_lot": str(self.short_margin_per_lot),
            "source_id": self.source_id, "source_sha256": self.source_sha256,
            "market": self.market, "underlying_kind": self.underlying_kind,
            "exercise_style": self.exercise_style,
        }


class OptionsPaperAccount:
    """Single-currency append-only options book; expiry assignments reconcile cash and underlying inventory."""

    def __init__(self, path: Path, *, account_id: str, initial_cash: Decimal,
                 currency: str = "INR") -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", account_id or ""):
            raise OptionsPaperError("account_id must be a stable identifier")
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise OptionsPaperError("currency must be a three-letter uppercase code")
        cash = Decimal(initial_cash)
        if not cash.is_finite() or cash <= 0:
            raise OptionsPaperError("initial_cash must be positive and finite")
        self.path, self.account_id, self.currency = Path(path), account_id, currency
        self.journal = DurableJournal(self.path)
        rows = self.journal.records()
        opening = {"t": "OPTIONS_ACCOUNT_OPEN", "account_id": account_id,
                   "initial_cash": str(cash), "currency": currency}
        if not rows:
            self.journal.append(opening)
        elif rows[0] != opening:
            raise OptionsPaperError("existing options journal opening does not match account configuration")
        self._rebuild()

    @property
    def cash(self) -> Decimal:
        return self.ledger.balance(f"cash:{self.account_id}", self.currency)

    @property
    def realized_pnl(self) -> Decimal | None:
        """Return settled premium/expiry P&L only when no option position needs a missing mark."""
        if self._lots:
            return None
        return -self.ledger.balance(f"options_pnl:{self.account_id}", self.currency)

    @property
    def option_positions(self) -> dict[str, int]:
        return {key: lots for key, lots in self._lots.items() if lots}

    @property
    def open_option_positions(self) -> dict[str, tuple[OptionContract, int]]:
        return {key: (self._contracts[key], lots) for key, lots in self._lots.items() if lots}

    @property
    def legacy_expiry_events(self) -> tuple[str, ...]:
        """Historical settlements created before India expiry preflight became mandatory."""
        return tuple(self._legacy_expiry_events)

    def preflight_india_expiry(self, *, settlement_prices: dict[str, Decimal],
                               session_date: date, settlement_source_sha256: str,
                               delivery_margins=None):
        from .india_options_expiry import preflight_india_expiry

        return preflight_india_expiry(
            self, settlement_prices=settlement_prices, session_date=session_date,
            settlement_source_sha256=settlement_source_sha256,
            delivery_margins=delivery_margins,
        )

    @property
    def underlying_positions(self) -> dict[str, int]:
        return {key: quantity for key, quantity in self._underlying.items() if quantity}

    @property
    def margin_required(self) -> Decimal:
        """Conservative spread-width reserve; unmatched shorts use supplied per-lot margin terms."""
        longs: dict[str, int] = {key: lots for key, lots in self._lots.items() if lots > 0}
        shorts = [(self._contracts[key], -lots) for key, lots in self._lots.items() if lots < 0]
        reserve = Decimal(0)
        for short, count in sorted(shorts, key=lambda row: row[0].contract_id):
            candidates = [self._contracts[key] for key, available in longs.items() if available > 0
                          and self._compatible_spread(short, self._contracts[key])]
            candidates.sort(key=lambda long: (abs(long.strike - short.strike), long.contract_id))
            for long in candidates:
                matched = min(count, longs[long.contract_id])
                reserve += abs(long.strike - short.strike) * Decimal(short.lot_size * matched)
                count -= matched
                longs[long.contract_id] -= matched
                if not count:
                    break
            reserve += short.short_margin_per_lot * count
        return _money(reserve)

    @property
    def available_cash(self) -> Decimal:
        return self.cash - self.margin_required

    @property
    def margin_call(self) -> bool:
        return self.cash < self.margin_required

    @property
    def equity(self) -> Decimal | None:
        if self._lots:
            return None
        return _money(self.cash + sum((Decimal(qty) * self._underlying_marks[symbol]
                                      for symbol, qty in self._underlying.items()), Decimal(0)))

    def trade(self, event_id: str, contract: OptionContract, *, quantity_lots: int,
              premium: Decimal, session_date: date, source_sha256: str) -> None:
        """Buy positive or sell negative lots at a premium per underlying unit."""
        premium = Decimal(premium)
        self._validate_event_id(event_id)
        self._validate_source(source_sha256)
        if not premium.is_finite() or premium < 0:
            raise OptionsPaperError("option premium must be finite and non-negative")
        if type(quantity_lots) is not int or quantity_lots == 0:
            raise OptionsPaperError("quantity_lots must be a nonzero integer")
        row = self._event_row("OPTION_TRADE", event_id, {
            "contract": contract.as_dict(), "quantity_lots": quantity_lots,
            "premium": str(premium), "session_date": session_date.isoformat(),
            "source_sha256": source_sha256,
        })
        if self._is_duplicate(row):
            return
        if session_date > contract.expiry:
            raise OptionsPaperError("expired contract cannot be traded")
        if self._last_event_date is not None and session_date < self._last_event_date:
            raise OptionsPaperError("option events must be recorded in effective-date order")
        if contract.contract_id in self._expired:
            raise OptionsPaperError("expired option contract cannot be reopened")
        existing = self._contracts.get(contract.contract_id)
        if existing is not None and existing != contract:
            raise OptionsPaperError("contract ID is already bound to different terms")
        self._commit(row)

    def settle_expiry(self, event_id: str, *, settlement_prices: dict[str, Decimal],
                      session_date: date, source_sha256: str, readiness=None) -> None:
        """Exercise/assign ITM options, book expiry cash obligations, and mark physical shares at settlement."""
        self._validate_event_id(event_id)
        self._validate_source(source_sha256)
        prices = {symbol: Decimal(value) for symbol, value in settlement_prices.items()}
        if not prices or any(not value.is_finite() or value <= 0 for value in prices.values()):
            raise OptionsPaperError("positive finite settlement prices are required")
        from .india_options_expiry import readiness_matches
        from dataclasses import asdict

        readiness_payload = asdict(readiness) if readiness is not None else None
        if readiness_payload is not None:
            readiness_payload["session_date"] = readiness.session_date.isoformat()
        row = self._event_row("OPTION_EXPIRY", event_id, {
            "session_date": session_date.isoformat(), "settlement_prices": {k: str(v) for k, v in sorted(prices.items())},
            "source_sha256": source_sha256, "india_expiry_preflight": readiness_payload,
        })
        if self._is_duplicate(row):
            return
        if not readiness_matches(self, readiness, settlement_prices=prices,
                                 session_date=session_date, settlement_source_sha256=source_sha256):
            raise OptionsPaperError("valid India expiry preflight is required before settlement")
        if self._last_event_date is not None and session_date < self._last_event_date:
            raise OptionsPaperError("option events must be recorded in effective-date order")
        expiring = [key for key, lots in self._lots.items() if lots and self._contracts[key].expiry == session_date]
        if not expiring:
            raise OptionsPaperError("no open options expire on the requested date")
        underlyings = {self._contracts[key].underlying for key in expiring}
        if not underlyings.issubset(prices):
            raise OptionsPaperError("a settlement price is required for every expiring underlying")
        self._commit(row)

    def _rebuild(self) -> None:
        self.ledger = DoubleEntryLedger()
        rows = self.journal.records()
        opening = rows[0]
        self.ledger.post(Transaction("options-opening", (
            Entry(f"cash:{self.account_id}", DEBIT, Money.of(opening["initial_cash"], self.currency)),
            Entry(f"capital:{self.account_id}", CREDIT, Money.of(opening["initial_cash"], self.currency)),
        )))
        self._contracts: dict[str, OptionContract] = {}
        self._lots: dict[str, int] = {}
        self._underlying: dict[str, int] = {}
        self._underlying_marks: dict[str, Decimal] = {}
        self._expired: set[str] = set()
        self._legacy_expiry_events: list[str] = []
        self._last_event_date: date | None = None
        self._event_requests: dict[str, str] = {}
        for row in rows[1:]:
            if row.get("t") == "OPTION_TRADE":
                self._apply_trade(row)
            elif row.get("t") == "OPTION_EXPIRY":
                if row.get("india_expiry_preflight") is None:
                    self._legacy_expiry_events.append(row["event_id"])
                self._apply_expiry(row)
            else:
                raise OptionsPaperError(f"unknown options journal event: {row.get('t')}")
        if not self.ledger.trial_balance_ok():
            raise OptionsPaperError("options double-entry ledger does not balance")

    def _apply_trade(self, row: dict) -> None:
        event_id, digest = row["event_id"], row["request_sha256"]
        self._check_request(event_id, digest)
        contract = _contract_from_dict(row["contract"])
        quantity, premium = int(row["quantity_lots"]), Decimal(row["premium"])
        day = date.fromisoformat(row["session_date"])
        if day > contract.expiry or self._last_event_date is not None and day < self._last_event_date:
            raise OptionsPaperError("durable trade date is outside the contract lifecycle")
        existing = self._contracts.get(contract.contract_id)
        if existing is not None and existing != contract:
            raise OptionsPaperError("durable contract ID changed terms")
        if contract.contract_id in self._expired:
            raise OptionsPaperError("durable journal reopens an expired option")
        self._contracts[contract.contract_id] = contract
        old_lots = self._lots.get(contract.contract_id, 0)
        new_lots = old_lots + quantity
        cash_delta = _money(-Decimal(quantity * contract.lot_size) * premium)
        self._post_cash_pnl(f"option-premium:{event_id}", cash_delta)
        if new_lots:
            self._lots[contract.contract_id] = new_lots
        else:
            self._lots.pop(contract.contract_id, None)
        if self.cash < self.margin_required:
            raise OptionsPaperError("trade would leave insufficient cash for option margin reserve")
        self._last_event_date = day
        self._event_requests[event_id] = digest

    def _apply_expiry(self, row: dict) -> None:
        event_id, digest = row["event_id"], row["request_sha256"]
        self._check_request(event_id, digest)
        day = date.fromisoformat(row["session_date"])
        prices = {key: Decimal(value) for key, value in row["settlement_prices"].items()}
        if row.get("india_expiry_preflight") is not None:
            from .india_options_expiry import verify_stored_readiness

            if not verify_stored_readiness(self, row):
                raise OptionsPaperError("durable India expiry preflight does not reconcile")
        contracts = [key for key, lots in self._lots.items() if lots and self._contracts[key].expiry == day]
        if not contracts or self._last_event_date is not None and day < self._last_event_date:
            raise OptionsPaperError("expiry event has no eligible open contracts or is stale")
        if any(self._contracts[key].underlying not in prices for key in contracts):
            raise OptionsPaperError("expiry event omits an underlying settlement price")
        cash_delta: dict[str, Decimal] = {}
        underlying_delta: dict[str, int] = {}
        pnl_delta = Decimal(0)
        for key in contracts:
            contract, lots = self._contracts[key], self._lots[key]
            spot = prices[contract.underlying]
            self._positive(spot, "settlement spot")
            intrinsic = max(spot - contract.strike, Decimal(0)) if contract.kind == "CALL" else \
                max(contract.strike - spot, Decimal(0))
            if intrinsic:
                if contract.settlement == "CASH":
                    flow = _money(Decimal(lots * contract.lot_size) * intrinsic)
                    cash_delta[contract.underlying] = cash_delta.get(contract.underlying, Decimal(0)) + flow
                    pnl_delta += flow
                else:
                    direction = 1 if contract.kind == "CALL" else -1
                    share_delta = lots * contract.lot_size * direction
                    underlying_delta[contract.underlying] = underlying_delta.get(contract.underlying, 0) + share_delta
                    flow = _money(-Decimal(share_delta) * contract.strike)
                    cash_delta[contract.underlying] = cash_delta.get(contract.underlying, Decimal(0)) + flow
            self._expired.add(key)
            self._lots.pop(key)

        inventory_delta = Decimal(0)
        for symbol in set(prices):
            if symbol not in self._underlying and symbol not in underlying_delta:
                continue
            old_quantity = self._underlying.get(symbol, 0)
            old_mark = self._underlying_marks.get(symbol, prices[symbol])
            new_quantity = old_quantity + underlying_delta.get(symbol, 0)
            old_value = Decimal(old_quantity) * old_mark
            new_value = Decimal(new_quantity) * prices[symbol]
            change = _money(new_value - old_value)
            inventory_delta += change
            if new_quantity:
                self._underlying[symbol] = new_quantity
                self._underlying_marks[symbol] = prices[symbol]
            else:
                self._underlying.pop(symbol, None)
                self._underlying_marks.pop(symbol, None)
        total_cash = _money(sum(cash_delta.values(), Decimal(0)))
        total_pnl = _money(total_cash + inventory_delta)
        self._post_asset_event(f"option-expiry:{event_id}", total_cash, inventory_delta, total_pnl)
        self._last_event_date = day
        self._event_requests[event_id] = digest

    def _post_cash_pnl(self, txn_id: str, cash_delta: Decimal) -> None:
        if not cash_delta:
            return
        amount = Money.of(abs(cash_delta), self.currency)
        if cash_delta > 0:
            entries = (Entry(f"cash:{self.account_id}", DEBIT, amount),
                       Entry(f"options_pnl:{self.account_id}", CREDIT, amount))
        else:
            entries = (Entry(f"options_pnl:{self.account_id}", DEBIT, amount),
                       Entry(f"cash:{self.account_id}", CREDIT, amount))
        self.ledger.post(Transaction(txn_id, entries))

    def _post_asset_event(self, txn_id: str, cash_delta: Decimal,
                          inventory_delta: Decimal, pnl_delta: Decimal) -> None:
        entries: list[Entry] = []
        if cash_delta:
            entries.append(Entry(f"cash:{self.account_id}", DEBIT if cash_delta > 0 else CREDIT,
                                 Money.of(abs(cash_delta), self.currency)))
        # A consolidated underlying inventory control balance is valued at the current expiry mark.
        if inventory_delta:
            entries.append(Entry(f"underlying_inventory:{self.account_id}",
                                 DEBIT if inventory_delta > 0 else CREDIT,
                                 Money.of(abs(inventory_delta), self.currency)))
        if pnl_delta:
            entries.append(Entry(f"options_pnl:{self.account_id}",
                                 CREDIT if pnl_delta > 0 else DEBIT,
                                 Money.of(abs(pnl_delta), self.currency)))
        if entries:
            self.ledger.post(Transaction(txn_id, tuple(entries)))

    @staticmethod
    def _compatible_spread(short: OptionContract, long: OptionContract) -> bool:
        return (short.underlying, short.kind, short.expiry, short.lot_size, short.settlement) == (
            long.underlying, long.kind, long.expiry, long.lot_size, long.settlement
        )

    def _commit(self, row: dict) -> None:
        existing = next((record for record in self.journal.records()
                         if record.get("event_id") == row["event_id"]), None)
        if existing is not None:
            if existing.get("request_sha256") != row["request_sha256"]:
                raise OptionsPaperError("event id reused with different options request")
            return
        preview = self.path.with_name(f"{self.path.name}.{uuid4().hex}.preview")
        preview.write_bytes(self.path.read_bytes())
        try:
            candidate = DurableJournal(preview)
            candidate.append(row)
            view = object.__new__(OptionsPaperAccount)
            view.path, view.account_id, view.currency, view.journal = self.path, self.account_id, self.currency, candidate
            view._rebuild()
            self.journal.append(row)
            self._rebuild()
        finally:
            preview.unlink(missing_ok=True)

    def _is_duplicate(self, row: dict) -> bool:
        existing = next((record for record in self.journal.records()
                         if record.get("event_id") == row["event_id"]), None)
        if existing is None:
            return False
        if existing.get("request_sha256") != row["request_sha256"]:
            raise OptionsPaperError("event id reused with different options request")
        return True

    @staticmethod
    def _event_row(kind: str, event_id: str, request: dict) -> dict:
        payload = {"event_type": kind, "event_id": event_id, **request}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                             allow_nan=False).encode("utf-8")).hexdigest()
        return {"t": kind, **payload, "request_sha256": digest, "physical_orders": False}

    def _check_request(self, event_id: str, digest: str) -> None:
        prior = self._event_requests.get(event_id)
        if prior is not None and prior != digest:
            raise OptionsPaperError("option event ID binds conflicting request content")

    @staticmethod
    def _validate_event_id(event_id: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", event_id or ""):
            raise OptionsPaperError("event id must be stable")

    @staticmethod
    def _validate_source(source_sha256: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", source_sha256 or ""):
            raise OptionsPaperError("source SHA-256 is required")

    @staticmethod
    def _positive(value: Decimal, name: str) -> None:
        if not value.is_finite() or value <= 0:
            raise OptionsPaperError(f"{name} must be positive and finite")


def _contract_from_dict(value: dict) -> OptionContract:
    return OptionContract(
        contract_id=value["contract_id"], underlying=value["underlying"], kind=value["kind"],
        strike=Decimal(value["strike"]), expiry=date.fromisoformat(value["expiry"]),
        lot_size=int(value["lot_size"]), settlement=value["settlement"],
        short_margin_per_lot=Decimal(value["short_margin_per_lot"]), source_id=value["source_id"],
        source_sha256=value["source_sha256"], market=value.get("market", "NFO"),
        underlying_kind=value.get("underlying_kind", "STOCK"),
        exercise_style=value.get("exercise_style", "EUROPEAN"),
    )


def _money(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise OptionsPaperError("option cash amount must be finite")
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
