"""Durable, single-currency paper book backed by one OMS and one double-entry ledger.

The append-only journal is the source of truth. The OMS supplies buying-power and order
state controls; the ledger supplies cash, inventory cost basis, fees, and realized P&L.
Reopening an account folds the same events into both views and checks that cash reconciles.

This first version intentionally supports long-only cash instruments in one currency. It
does not model taxes, settlement cycles, margin, derivatives, or exchange execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import re
from uuid import uuid4

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.kernel.ledger import CREDIT, DEBIT, DoubleEntryLedger, Entry, Transaction
from financial_brain.kernel.oms import Oms, OmsError, State
from financial_brain.kernel.units import Money


class PaperAccountError(ValueError):
    """A paper account command is invalid or cannot be reconciled."""


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: int
    average_cost: Decimal


@dataclass(frozen=True)
class FeeSchedule:
    """Owner-supplied, effective-dated all-in cash-equity fee rates; no market rates are hardcoded."""

    schedule_id: str
    effective_from: date
    buy_bps: Decimal
    sell_bps: Decimal
    minimum_per_fill: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if not self.schedule_id.strip():
            raise PaperAccountError("fee schedule id is required")
        for name, amount in (("buy_bps", self.buy_bps), ("sell_bps", self.sell_bps),
                             ("minimum_per_fill", self.minimum_per_fill)):
            if not isinstance(amount, Decimal) or not amount.is_finite() or amount < 0:
                raise PaperAccountError(f"{name} must be a finite nonnegative Decimal")

    def calculate(self, *, side: str, notional: Decimal) -> Decimal:
        if side not in {"BUY", "SELL"} or notional <= 0 or not notional.is_finite():
            raise PaperAccountError("fee calculation needs a valid side and positive finite notional")
        bps = self.buy_bps if side == "BUY" else self.sell_bps
        raw = max(self.minimum_per_fill, notional * bps / Decimal("10000"))
        return raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def as_dict(self) -> dict:
        return {"schedule_id": self.schedule_id, "effective_from": self.effective_from.isoformat(),
                "buy_bps": str(self.buy_bps), "sell_bps": str(self.sell_bps),
                "minimum_per_fill": str(self.minimum_per_fill)}

    @classmethod
    def from_dict(cls, value: dict | None) -> FeeSchedule | None:
        if value is None:
            return None
        return cls(value["schedule_id"], date.fromisoformat(value["effective_from"]),
                   Decimal(value["buy_bps"]), Decimal(value["sell_bps"]),
                   Decimal(value["minimum_per_fill"]))


class PaperAccount:
    """Durable paper book for one account and currency; no broker or live-order path exists."""

    def __init__(self, path: Path, *, account_id: str, initial_cash: Decimal, currency: str = "INR",
                 fee_schedule: FeeSchedule | None = None) -> None:
        if not account_id or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", account_id):
            raise PaperAccountError("account_id must be a non-empty stable identifier")
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise PaperAccountError("currency must be a three-letter uppercase code")
        self.path = Path(path)
        self.account_id = account_id
        self.currency = currency
        self.journal = DurableJournal(self.path)
        records = self.journal.records()
        cash = Decimal(initial_cash)
        if not cash.is_finite() or cash <= 0:
            raise PaperAccountError("initial_cash must be a positive finite Decimal")
        schedule_data = fee_schedule.as_dict() if fee_schedule is not None else None
        opening = {"t": "ACCOUNT_OPEN", "account": account_id, "cash": format(cash, "f"),
                   "currency": currency, "fee_schedule": schedule_data}
        if not records:
            self.journal.append(opening)
        else:
            stored = records[0]
            if (stored.get("t") != "ACCOUNT_OPEN" or stored.get("account") != account_id
                    or stored.get("currency") != currency or Decimal(stored.get("cash", "NaN")) != cash):
                raise PaperAccountError("existing journal opening does not match account configuration")
            stored_schedule = stored.get("fee_schedule")
            if fee_schedule is not None and stored_schedule != schedule_data:
                raise PaperAccountError("existing journal fee schedule does not match configuration")
            fee_schedule = FeeSchedule.from_dict(stored_schedule)
        self.fee_schedule = fee_schedule
        self._rebuild()

    @property
    def cash(self) -> Decimal:
        return self.oms.cash(self.account_id)

    @property
    def available_cash(self) -> Decimal:
        return self.oms.available(self.account_id)

    @property
    def realized_pnl(self) -> Decimal:
        # Credit balances are positive economic P&L; ledger.balance is signed debits-minus-credits.
        return -self.ledger.balance(f"realized_pnl:{self.account_id}", self.currency)

    @property
    def net_realized_pnl(self) -> Decimal:
        return self.realized_pnl - self.fees

    @property
    def fees(self) -> Decimal:
        return self.ledger.balance(f"fees:{self.account_id}", self.currency)

    @property
    def equity(self) -> Decimal:
        missing = sorted(symbol for symbol, (qty, _) in self._positions.items()
                         if qty and symbol not in self._marks)
        if missing:
            raise PaperAccountError(f"NAV is unavailable without current marks for: {', '.join(missing)}")
        return self.cash + sum((Decimal(qty) * self._marks[symbol][0]
                                for symbol, (qty, _) in self._positions.items()), Decimal(0))

    @property
    def net_pnl(self) -> Decimal:
        return self.equity - self._initial_cash

    def position(self, symbol: str) -> Position:
        quantity, basis = self._positions.get(symbol, (0, Decimal(0)))
        return Position(symbol, quantity, basis / quantity if quantity else Decimal(0))

    def _rebuild(self) -> None:
        records = self.journal.records()
        try:
            opening = records[0]
            self.fee_schedule = FeeSchedule.from_dict(opening.get("fee_schedule"))
            self._initial_cash = Decimal(opening["cash"])
            self.oms = Oms({self.account_id: Decimal(opening["cash"])})
            self.ledger = DoubleEntryLedger()
            self.ledger.post(Transaction("account-opening", (
                Entry(f"cash:{self.account_id}", DEBIT, Money.of(opening["cash"], self.currency)),
                Entry(f"capital:{self.account_id}", CREDIT, Money.of(opening["cash"], self.currency)),
            )))
            self._positions: dict[str, tuple[int, Decimal]] = {}
            self._sell_reserved: dict[str, int] = {}
            self._fill_ids: set[str] = set()
            self._fill_content: dict[str, tuple] = {}
            self._fee_ids: dict[str, Decimal] = {}
            self._marks: dict[str, tuple[Decimal, datetime, str]] = {}
            self._mark_ids: dict[str, tuple] = {}
            for r in records[1:]:
                t = r["t"]
                if t == "INTENT":
                    self.oms.submit_intent(
                        client_id=r["client_id"], account=self.account_id, side=r["side"],
                        quantity=r["quantity"], limit=Decimal(r["limit"]) if r["limit"] is not None else None,
                        reserve=Decimal(r["reserve"]),
                    )
                    if r["side"] == "SELL":
                        self._sell_reserved[r["client_id"]] = r["quantity"]
                elif t == "STATE":
                    order = self.oms.order(r["client_id"])
                    old = order.state
                    self.oms.move(r["client_id"], State(r["state"]))
                    if order.side == "SELL" and order.state in {State.CANCELED, State.REJECTED, State.EXPIRED}:
                        self._sell_reserved[r["client_id"]] = 0
                    if old is State.CANCEL_PENDING and order.state is State.FILLED:
                        self._sell_reserved[r["client_id"]] = 0
                elif t == "FILL":
                    self._apply_fill(r, post_to_oms=True)
                elif t == "FEE":
                    self._apply_fee(r, post_to_oms=True)
                elif t == "MARK":
                    self._apply_mark(r)
                else:
                    raise PaperAccountError(f"unknown paper account journal event: {t}")
            if not self.ledger.trial_balance_ok():
                raise PaperAccountError("paper ledger trial balance does not reconcile")
            if self.ledger.balance(f"cash:{self.account_id}", self.currency) != self.cash:
                raise PaperAccountError("OMS cash and double-entry cash do not reconcile")
        except OmsError:
            raise
        except (KeyError, ValueError) as exc:
            if isinstance(exc, PaperAccountError):
                raise
            raise PaperAccountError(f"cannot replay paper account journal: {exc}") from exc
        self._release_terminal_sell_reserves()

    def _release_terminal_sell_reserves(self) -> None:
        for cid, order in self.oms._orders.items():
            if order.side == "SELL" and order.state in {State.CANCELED, State.REJECTED, State.EXPIRED, State.FILLED}:
                self._sell_reserved[cid] = 0

    def _validate_and_commit(self, rec: dict) -> None:
        # Preview through the same deterministic fold before making the event durable.
        # A rejected command therefore leaves journal and account state unchanged.
        preview = self.path.with_name(f"{self.path.name}.{uuid4().hex}.preview")
        preview.write_bytes(self.path.read_bytes())
        try:
            candidate = DurableJournal(preview)
            candidate.append(rec)
            candidate_book = object.__new__(PaperAccount)
            candidate_book.path = self.path
            candidate_book.account_id = self.account_id
            candidate_book.currency = self.currency
            candidate_book.fee_schedule = self.fee_schedule
            candidate_book.journal = candidate
            candidate_book._rebuild()
            self.journal.append(rec)
            self._rebuild()
        finally:
            preview.unlink(missing_ok=True)

    def submit_intent(
        self, *, client_id: str, symbol: str, side: str, quantity: int,
        limit: Decimal | None, reserve: Decimal,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", symbol or ""):
            raise PaperAccountError("symbol must be a stable identifier")
        side = side.upper()
        if side not in {"BUY", "SELL"} or type(quantity) is not int or quantity <= 0:
            raise PaperAccountError("invalid side or quantity")
        if side == "BUY" and limit is not None and reserve < limit * quantity:
            raise PaperAccountError("buy reserve must cover the limit notional")
        if side == "BUY" and limit is not None and self.fee_schedule is not None:
            minimum_buffer = self.fee_schedule.calculate(side="BUY", notional=limit * quantity)
            if reserve < limit * quantity + minimum_buffer:
                raise PaperAccountError("buy reserve must also cover the scheduled fee buffer")
        existing = next((r for r in self.journal.records()
                         if r.get("t") == "INTENT" and r.get("client_id") == client_id), None)
        if existing is not None:
            proposed = {
                "t": "INTENT", "client_id": client_id, "symbol": symbol, "side": side,
                "quantity": quantity, "limit": str(limit) if limit is not None else None,
                "reserve": str(reserve),
            }
            if existing != proposed:
                raise PaperAccountError(f"client id {client_id} reused with different content")
            return
        if side == "SELL" and self.position(symbol).quantity - sum(
            qty for cid, qty in self._sell_reserved.items()
            if self.oms.order(cid).state not in {State.CANCELED, State.REJECTED, State.EXPIRED, State.FILLED}
        ) < quantity:
            raise PaperAccountError("sell quantity exceeds unreserved long inventory")
        rec = {
            "t": "INTENT", "client_id": client_id, "symbol": symbol, "side": side,
            "quantity": quantity, "limit": str(limit) if limit is not None else None,
            "reserve": str(reserve),
        }
        self._validate_and_commit(rec)

    def move(self, client_id: str, state: State) -> None:
        self._validate_and_commit({"t": "STATE", "client_id": client_id, "state": state.value})

    def record_fill(self, client_id: str, *, fill_id: str, quantity: int, price: Decimal,
                    filled_on: date | None = None) -> None:
        order = self.oms.order(client_id)
        fee_amount = Decimal(0)
        if self.fee_schedule is not None:
            if filled_on is None:
                raise PaperAccountError("filled_on is required by the effective-dated fee schedule")
            if filled_on < self.fee_schedule.effective_from:
                raise PaperAccountError("fee schedule is not yet effective on the fill date")
            fee_amount = self.fee_schedule.calculate(side=order.side, notional=price * quantity)
        rec = {
            "t": "FILL", "client_id": client_id, "fill_id": fill_id,
            "symbol": self._symbol_for(client_id), "side": order.side,
            "quantity": quantity, "price": str(price), "fee_amount": str(fee_amount),
            "fee_schedule_id": self.fee_schedule.schedule_id if self.fee_schedule else None,
            "filled_on": filled_on.isoformat() if filled_on else None,
        }
        content = (client_id, rec["symbol"], quantity, str(price), str(fee_amount),
                   rec["fee_schedule_id"], rec["filled_on"])
        if fill_id in self._fill_content:
            if self._fill_content[fill_id] != content:
                raise PaperAccountError(f"fill id {fill_id} reused with different content")
            return
        self._validate_and_commit(rec)

    def record_mark(self, symbol: str, *, mark_id: str, price: Decimal, observed_at: datetime) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", symbol or "") or not mark_id:
            raise PaperAccountError("mark needs a stable symbol and id")
        if not price.is_finite() or price <= 0:
            raise PaperAccountError("mark price must be positive and finite")
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise PaperAccountError("mark time must be timezone-aware")
        row = {"symbol": symbol, "price": str(price),
               "observed_at": observed_at.astimezone(timezone.utc).isoformat()}
        prior = self._mark_ids.get(mark_id)
        if prior is not None:
            if prior != (symbol, str(price), row["observed_at"]):
                raise PaperAccountError(f"mark id {mark_id} reused with different content")
            return
        self._validate_and_commit({"t": "MARK", "mark_id": mark_id, **row})

    def record_fee(self, fee_id: str, amount: Decimal) -> None:
        fee = Decimal(amount)
        if not fee_id or not fee.is_finite() or fee <= 0:
            raise PaperAccountError("fee id and positive amount are required")
        if fee_id in self._fee_ids:
            if self._fee_ids[fee_id] != fee:
                raise PaperAccountError(f"fee id {fee_id} reused with different amount")
            return
        self._validate_and_commit({"t": "FEE", "fee_id": fee_id, "amount": str(fee)})

    def _symbol_for(self, client_id: str) -> str:
        for r in self.journal.records():
            if r.get("t") == "INTENT" and r.get("client_id") == client_id:
                return r["symbol"]
        raise PaperAccountError(f"unknown order {client_id}")

    def _apply_fill(self, r: dict, *, post_to_oms: bool) -> None:
        fill_id = r["fill_id"]
        order = self.oms.order(r["client_id"])
        prior: set[str] = getattr(self, "_fill_ids", set())
        content = (r["client_id"], r["symbol"], r["quantity"], r["price"], r.get("fee_amount", "0"),
                   r.get("fee_schedule_id"), r.get("filled_on"))
        if fill_id in prior:
            if self._fill_content[fill_id] != content:
                raise PaperAccountError(f"fill id {fill_id} reused with different content")
            return
        qty, price = int(r["quantity"]), Decimal(r["price"])
        old_qty, old_basis = self._positions.get(r["symbol"], (0, Decimal(0)))
        amount = price * qty
        fee = Decimal(r.get("fee_amount", "0"))
        if not fee.is_finite() or fee < 0:
            raise PaperAccountError("fill fee must be finite and nonnegative")
        if self.fee_schedule is not None:
            if r.get("fee_schedule_id") != self.fee_schedule.schedule_id:
                raise PaperAccountError("fill fee schedule id does not match the account schedule")
            if not r.get("filled_on"):
                raise PaperAccountError("scheduled fill is missing its effective date")
            fill_date = date.fromisoformat(r["filled_on"])
            expected = self.fee_schedule.calculate(side=order.side, notional=amount)
            if fill_date < self.fee_schedule.effective_from or fee != expected:
                raise PaperAccountError("durable fill fee does not match its effective schedule")
        elif fee != 0:
            raise PaperAccountError("fill includes a fee without an account fee schedule")
        if order.side == "BUY":
            entries = [Entry(f"position:{self.account_id}:{r['symbol']}", DEBIT, Money.of(amount, self.currency))]
            if fee:
                entries.append(Entry(f"fees:{self.account_id}", DEBIT, Money.of(fee, self.currency)))
            entries.append(Entry(f"cash:{self.account_id}", CREDIT, Money.of(amount + fee, self.currency)))
            txn = Transaction(f"fill:{fill_id}", tuple(entries))
            next_position = (old_qty + qty, old_basis + amount)
        else:
            if qty > old_qty:
                raise PaperAccountError("sell fill exceeds long inventory")
            basis = old_basis * Decimal(qty) / Decimal(old_qty)
            entries = []
            cash_delta = amount - fee
            if cash_delta > 0:
                entries.append(Entry(f"cash:{self.account_id}", DEBIT, Money.of(cash_delta, self.currency)))
            elif cash_delta < 0:
                entries.append(Entry(f"cash:{self.account_id}", CREDIT, Money.of(-cash_delta, self.currency)))
            if fee:
                entries.append(Entry(f"fees:{self.account_id}", DEBIT, Money.of(fee, self.currency)))
            entries.append(Entry(f"position:{self.account_id}:{r['symbol']}", CREDIT, Money.of(basis, self.currency)))
            difference = amount - basis
            if difference > 0:
                entries.append(Entry(f"realized_pnl:{self.account_id}", CREDIT, Money.of(difference, self.currency)))
            elif difference < 0:
                entries.append(Entry(f"realized_pnl:{self.account_id}", DEBIT, Money.of(-difference, self.currency)))
            txn = Transaction(f"fill:{fill_id}", tuple(entries))
            next_position = (old_qty - qty, old_basis - basis)
        if post_to_oms:
            self.oms.record_fill(r["client_id"], qty, price, provider_fill_id=fill_id)
            if fee:
                self.oms.apply_cash_adjustment(self.account_id, -fee, adjustment_id=f"fill-fee:{fill_id}")
        self.ledger.post(txn)
        self._positions[r["symbol"]] = next_position
        self._fill_ids = prior | {fill_id}
        self._fill_content = {**getattr(self, "_fill_content", {}), fill_id: content}
        if order.side == "SELL":
            self._sell_reserved[r["client_id"]] = max(0, self._sell_reserved.get(r["client_id"], order.quantity) - qty)

    def _apply_mark(self, r: dict) -> None:
        mark_id = r["mark_id"]
        price = Decimal(r["price"])
        observed_at = datetime.fromisoformat(r["observed_at"])
        content = (r["symbol"], r["price"], r["observed_at"])
        prior = self._mark_ids.get(mark_id)
        if prior is not None:
            if prior != content:
                raise PaperAccountError(f"mark id {mark_id} reused with different content")
            return
        if not price.is_finite() or price <= 0 or observed_at.tzinfo is None:
            raise PaperAccountError("durable mark must have a positive price and aware timestamp")
        current = self._marks.get(r["symbol"])
        if current is None or observed_at >= current[1]:
            self._marks[r["symbol"]] = (price, observed_at, mark_id)
        self._mark_ids[mark_id] = content

    def _apply_fee(self, r: dict, *, post_to_oms: bool) -> None:
        fee_id, amount = r["fee_id"], Decimal(r["amount"])
        prior = getattr(self, "_fee_ids", {})
        if fee_id in prior:
            if prior[fee_id] != amount:
                raise PaperAccountError(f"fee id {fee_id} reused with different amount")
            return
        self.ledger.post(Transaction(f"fee:{fee_id}", (
            Entry(f"fees:{self.account_id}", DEBIT, Money.of(amount, self.currency)),
            Entry(f"cash:{self.account_id}", CREDIT, Money.of(amount, self.currency)),
        )))
        if post_to_oms:
            self.oms.apply_cash_adjustment(self.account_id, -amount, adjustment_id=fee_id)
        self._fee_ids = {**prior, fee_id: amount}

