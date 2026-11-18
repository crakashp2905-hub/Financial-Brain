"""Durable, single-currency paper book backed by one OMS and one double-entry ledger.

The append-only journal is the source of truth. The OMS supplies buying-power and order
state controls; the ledger supplies cash, inventory cost basis, fees, and realized P&L.
Reopening an account folds the same events into both views and checks that cash reconciles.

This first version intentionally supports long-only cash instruments in one currency. It
does not model taxes, settlement cycles, margin, derivatives, or exchange execution.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
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

    @property
    def sha256(self) -> str:
        canonical = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

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
    def initial_cash(self) -> Decimal:
        return self._initial_cash

    @property
    def available_cash(self) -> Decimal:
        return self.oms.available(self.account_id)

    @property
    def realized_pnl(self) -> Decimal:
        # Credit balances are positive economic P&L; ledger.balance is signed debits-minus-credits.
        return -self.ledger.balance(f"realized_pnl:{self.account_id}", self.currency)

    @property
    def net_realized_pnl(self) -> Decimal:
        return self.realized_pnl + self.dividend_income - self.fees - self.taxes

    @property
    def fees(self) -> Decimal:
        return self.ledger.balance(f"fees:{self.account_id}", self.currency)

    @property
    def dividend_income(self) -> Decimal:
        return -self.ledger.balance(f"dividend_income:{self.account_id}", self.currency)

    @property
    def taxes(self) -> Decimal:
        return self.ledger.balance(f"taxes:{self.account_id}", self.currency)

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

    @property
    def nav_snapshots(self) -> list[dict]:
        return deepcopy(self._nav_snapshots)

    @property
    def daily_nav_returns(self) -> list[Decimal]:
        """Portfolio returns from consecutive persisted NAVs, net of flows between snapshots."""
        rows = self._nav_snapshots
        out = []
        for previous, current in zip(rows, rows[1:]):
            opening_nav = Decimal(previous["nav"])
            if opening_nav <= 0:
                raise PaperAccountError("previous NAV must be positive")
            out.append((Decimal(current["nav"]) - opening_nav - Decimal(current["external_flow"])) / opening_nav)
        return out

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
            self._corporate_action_ids: dict[str, tuple] = {}
            self._instrument_suspended: dict[str, bool] = {}
            self._marks: dict[str, tuple[Decimal, datetime, str]] = {}
            self._mark_ids: dict[str, tuple] = {}
            self._external_flows: list[dict] = []
            self._flow_ids: set[str] = set()
            self._flow_content: dict[str, tuple] = {}
            self._flow_since_snapshot = Decimal(0)
            self._last_flow_at: datetime | None = None
            self._nav_snapshots: list[dict] = []
            self._nav_snapshot_ids: dict[str, tuple] = {}
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
                elif t == "EXTERNAL_FLOW":
                    self._apply_external_flow(r, post_to_oms=True)
                elif t == "NAV_SNAPSHOT":
                    self._apply_nav_snapshot(r)
                elif t == "STOCK_SPLIT":
                    self._apply_stock_split(r)
                elif t == "CASH_DIVIDEND":
                    self._apply_cash_dividend(r)
                elif t == "INSTRUMENT_STATUS":
                    self._apply_instrument_status(r)
                elif t == "DELISTING_SETTLEMENT":
                    self._apply_delisting_settlement(r)
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
        if self._instrument_suspended.get(symbol, False):
            raise PaperAccountError("instrument is suspended or delisted; new paper intents are blocked")
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
                    filled_on: date | None = None, execution_evidence: dict | None = None) -> None:
        order = self.oms.order(client_id)
        fee_amount = Decimal(0)
        if self.fee_schedule is not None:
            if filled_on is None:
                raise PaperAccountError("filled_on is required by the effective-dated fee schedule")
            if filled_on < self.fee_schedule.effective_from:
                raise PaperAccountError("fee schedule is not yet effective on the fill date")
            fee_amount = self.fee_schedule.calculate(side=order.side, notional=price * quantity)
        if execution_evidence is not None:
            required = {"execution_model_id", "execution_model_sha256", "fee_schedule_sha256",
                        "latency_model_sha256", "data_snapshot_sha256"}
            if (set(execution_evidence) != required
                    or not isinstance(execution_evidence.get("execution_model_id"), str)
                    or not execution_evidence["execution_model_id"].strip()
                    or any(not isinstance(execution_evidence.get(key), str)
                           or not re.fullmatch(r"[0-9a-f]{64}", execution_evidence[key])
                           for key in required - {"execution_model_id"})):
                raise PaperAccountError("execution evidence must include model, fees, latency and data hashes")
            if self.fee_schedule is None or execution_evidence["fee_schedule_sha256"] != self.fee_schedule.sha256:
                raise PaperAccountError("execution evidence fee hash does not match the account fee schedule")
        rec = {
            "t": "FILL", "client_id": client_id, "fill_id": fill_id,
            "symbol": self._symbol_for(client_id), "side": order.side,
            "quantity": quantity, "price": str(price), "fee_amount": str(fee_amount),
            "fee_schedule_id": self.fee_schedule.schedule_id if self.fee_schedule else None,
            "filled_on": filled_on.isoformat() if filled_on else None,
            "execution_evidence": dict(execution_evidence) if execution_evidence is not None else None,
        }
        content = (client_id, rec["symbol"], quantity, str(price), str(fee_amount),
                   rec["fee_schedule_id"], rec["filled_on"], rec["execution_evidence"])
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

    def record_external_cash_flow(self, flow_id: str, amount: Decimal, *, occurred_at: datetime,
                                  reference: str) -> None:
        """Record a deposit/withdrawal with its effective time before the next NAV snapshot."""
        amount = Decimal(amount)
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", flow_id or ""):
            raise PaperAccountError("external flow needs a stable id")
        if not amount.is_finite() or amount == 0 or not isinstance(reference, str) or not reference.strip():
            raise PaperAccountError("external cash flow needs a finite nonzero amount and reference")
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise PaperAccountError("external flow time must be timezone-aware")
        occurred_at = occurred_at.astimezone(timezone.utc)
        content = (str(amount), occurred_at.isoformat(), reference)
        prior = self._flow_content.get(flow_id)
        if prior is not None:
            if prior != content:
                raise PaperAccountError(f"external flow id {flow_id} reused with different content")
            return
        if self._nav_snapshots and occurred_at <= datetime.fromisoformat(self._nav_snapshots[-1]["captured_at"]):
            raise PaperAccountError("late external flow cannot rewrite an already captured NAV date")
        self._validate_and_commit({"t": "EXTERNAL_FLOW", "flow_id": flow_id, "amount": str(amount),
                                   "occurred_at": occurred_at.isoformat(), "reference": reference})

    def record_nav_snapshot(self, session_date: date, *, snapshot_id: str, captured_at: datetime) -> dict:
        """Persist authoritative marked equity and the flows since the previous snapshot, once per exchange date."""
        if not isinstance(session_date, date) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", snapshot_id or ""):
            raise PaperAccountError("NAV snapshot needs a session date and stable id")
        if captured_at.tzinfo is None or captured_at.utcoffset() is None:
            raise PaperAccountError("NAV capture time must be timezone-aware")
        capture = captured_at.astimezone(timezone.utc)
        if isinstance(session_date, datetime):
            raise PaperAccountError("NAV session date must be a calendar date")
        prior = self._nav_snapshot_ids.get(snapshot_id)
        if prior is not None:
            existing = next(row for row in self._nav_snapshots if row["snapshot_id"] == snapshot_id)
            if existing["session_date"] != session_date.isoformat() or existing["captured_at"] != capture.isoformat():
                raise PaperAccountError(f"NAV snapshot id {snapshot_id} reused with different content")
            return deepcopy(existing)
        if any(row["session_date"] == session_date.isoformat() for row in self._nav_snapshots):
            raise PaperAccountError("a session already has an immutable NAV snapshot")
        future_marks = [symbol for symbol, (_, marked_at, _) in self._marks.items() if marked_at > capture]
        if future_marks or self._last_flow_at is not None and self._last_flow_at > capture:
            raise PaperAccountError("NAV snapshot cannot include marks or cash flows after its capture time")
        nav = self.equity
        flow = self._flow_since_snapshot
        record = {"t": "NAV_SNAPSHOT", "snapshot_id": snapshot_id, "session_date": session_date.isoformat(),
                  "captured_at": capture.isoformat(), "nav": str(nav), "external_flow": str(flow)}
        self._validate_and_commit(record)
        return next(row for row in self._nav_snapshots if row["snapshot_id"] == snapshot_id)

    def record_fee(self, fee_id: str, amount: Decimal) -> None:
        fee = Decimal(amount)
        if not fee_id or not fee.is_finite() or fee <= 0:
            raise PaperAccountError("fee id and positive amount are required")
        if fee_id in self._fee_ids:
            if self._fee_ids[fee_id] != fee:
                raise PaperAccountError(f"fee id {fee_id} reused with different amount")
            return
        self._validate_and_commit({"t": "FEE", "fee_id": fee_id, "amount": str(fee)})

    def apply_stock_split(self, action_id: str, symbol: str, *, ratio: Decimal, effective_on: date,
                          source_id: str, source_sha256: str) -> None:
        """Apply an evidenced split ratio; fractional-share outcomes are refused for owner resolution."""
        ratio = Decimal(ratio)
        self._validate_corporate_action(action_id, symbol, effective_on, source_id, source_sha256)
        quantity = self.position(symbol).quantity
        if quantity <= 0 or not ratio.is_finite() or ratio <= 0:
            raise PaperAccountError("stock split needs a held long position and positive finite ratio")
        new_quantity = Decimal(quantity) * ratio
        if new_quantity != new_quantity.to_integral_value():
            raise PaperAccountError("split creates fractional shares; an evidenced cash-in-lieu resolution is required")
        self._require_no_working_orders(symbol)
        self._require_action_before_trading(symbol, effective_on)
        row = {"t": "STOCK_SPLIT", "action_id": action_id, "symbol": symbol,
               "ratio": str(ratio), "effective_on": effective_on.isoformat(),
               "source_id": source_id, "source_sha256": source_sha256,
               "old_quantity": quantity, "new_quantity": int(new_quantity)}
        self._commit_corporate_action(row)

    def apply_cash_dividend(self, action_id: str, symbol: str, *, ex_date: date, payment_date: date,
                            eligible_quantity: int, gross_per_share: Decimal, withholding_rate: Decimal,
                            source_id: str, source_sha256: str) -> None:
        """Post owner-supplied gross dividend and withholding; the source must attest eligible shares and dates."""
        self._validate_corporate_action(action_id, symbol, payment_date, source_id, source_sha256)
        per_share, rate = Decimal(gross_per_share), Decimal(withholding_rate)
        if (not isinstance(ex_date, date) or isinstance(ex_date, datetime)
                or not isinstance(payment_date, date) or isinstance(payment_date, datetime)
                or type(eligible_quantity) is not int or eligible_quantity <= 0
                or not per_share.is_finite() or per_share <= 0 or not rate.is_finite() or not 0 <= rate <= 1
                or payment_date < ex_date):
            raise PaperAccountError("dividend dates, eligible quantity, gross rate or withholding are invalid")
        calculated_eligible = self._quantity_on_ex_date(symbol, ex_date)
        if eligible_quantity != calculated_eligible:
            raise PaperAccountError("dividend eligible quantity does not match the pre-ex-date paper position")
        gross = _round_paise(per_share * eligible_quantity)
        tax = _round_paise(gross * rate)
        if gross <= 0:
            raise PaperAccountError("dividend gross must be positive")
        row = {"t": "CASH_DIVIDEND", "action_id": action_id, "symbol": symbol,
               "ex_date": ex_date.isoformat(), "payment_date": payment_date.isoformat(),
               "eligible_quantity": eligible_quantity, "gross_per_share": str(per_share),
               "withholding_rate": str(rate), "gross_amount": str(gross), "tax_amount": str(tax),
               "source_id": source_id, "source_sha256": source_sha256}
        self._commit_corporate_action(row)

    def set_instrument_suspended(self, action_id: str, symbol: str, *, suspended: bool,
                                 effective_on: date, source_id: str, source_sha256: str) -> None:
        """Persist a sourced suspension/resumption status; suspended symbols reject new orders."""
        self._validate_corporate_action(action_id, symbol, effective_on, source_id, source_sha256)
        if type(suspended) is not bool:
            raise PaperAccountError("suspended must be a boolean")
        self._require_action_before_trading(symbol, effective_on)
        row = {"t": "INSTRUMENT_STATUS", "action_id": action_id, "symbol": symbol,
               "suspended": suspended, "effective_on": effective_on.isoformat(),
               "source_id": source_id, "source_sha256": source_sha256}
        self._commit_corporate_action(row)

    def settle_delisting(self, action_id: str, symbol: str, *, recovery_per_share: Decimal,
                         effective_on: date, source_id: str, source_sha256: str) -> None:
        """Settle a delisted long position at an explicitly sourced cash recovery, including zero recovery."""
        recovery = Decimal(recovery_per_share)
        self._validate_corporate_action(action_id, symbol, effective_on, source_id, source_sha256)
        quantity, basis = self._positions.get(symbol, (0, Decimal(0)))
        if quantity <= 0 or not recovery.is_finite() or recovery < 0:
            raise PaperAccountError("delisting needs a held long position and nonnegative finite recovery price")
        self._require_no_working_orders(symbol)
        self._require_action_before_trading(symbol, effective_on)
        proceeds = _round_paise(recovery * quantity)
        row = {"t": "DELISTING_SETTLEMENT", "action_id": action_id, "symbol": symbol,
               "effective_on": effective_on.isoformat(), "quantity": quantity, "basis": str(basis),
               "recovery_per_share": str(recovery), "proceeds": str(proceeds),
               "source_id": source_id, "source_sha256": source_sha256}
        self._commit_corporate_action(row)

    def _validate_corporate_action(self, action_id: str, symbol: str, effective_on: date,
                                   source_id: str, source_sha256: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", action_id or ""):
            raise PaperAccountError("corporate action needs a stable action id")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", symbol or ""):
            raise PaperAccountError("corporate action needs a stable symbol")
        if not isinstance(effective_on, date) or isinstance(effective_on, datetime):
            raise PaperAccountError("corporate action effective date must be a calendar date")
        if not isinstance(source_id, str) or not source_id.strip():
            raise PaperAccountError("corporate action source id is required")
        if not re.fullmatch(r"[0-9a-f]{64}", source_sha256 or ""):
            raise PaperAccountError("corporate action source SHA-256 is required")

    def _commit_corporate_action(self, row: dict) -> None:
        action_id = row["action_id"]
        digestable = {key: value for key, value in row.items() if key != "t"}
        content = tuple(sorted(digestable.items()))
        prior = self._corporate_action_ids.get(action_id)
        if prior is not None:
            if prior != content:
                raise PaperAccountError(f"corporate action id {action_id} reused with different content")
            return
        self._validate_and_commit(row)

    def _require_no_working_orders(self, symbol: str) -> None:
        for order in self.oms._orders.values():
            if (self._symbol_for(order.client_id) == symbol and order.remaining > 0
                    and order.state not in {State.CANCELED, State.REJECTED, State.EXPIRED, State.FILLED}):
                raise PaperAccountError("corporate action requires working orders to be canceled first")

    def _require_action_before_trading(self, symbol: str, effective_on: date) -> None:
        for row in self.journal.records():
            if (row.get("t") == "FILL" and row.get("symbol") == symbol and row.get("filled_on")
                    and date.fromisoformat(row["filled_on"]) >= effective_on):
                raise PaperAccountError("effective-date status/action must be recorded before that day's fills")

    def _quantity_on_ex_date(self, symbol: str, ex_date: date) -> int:
        events: list[tuple[date, int, int, dict]] = []
        for sequence, row in enumerate(self.journal.records()):
            if row.get("t") == "FILL" and row.get("symbol") == symbol:
                if not row.get("filled_on"):
                    raise PaperAccountError("cannot verify dividend eligibility without dated fills")
                filled_on = date.fromisoformat(row["filled_on"])
                if filled_on < ex_date:
                    events.append((filled_on, 1, sequence, row))
            elif row.get("t") == "STOCK_SPLIT" and row.get("symbol") == symbol:
                split_on = date.fromisoformat(row["effective_on"])
                if split_on <= ex_date:
                    events.append((split_on, 0, sequence, row))
            elif row.get("t") == "DELISTING_SETTLEMENT" and row.get("symbol") == symbol:
                delisted_on = date.fromisoformat(row["effective_on"])
                if delisted_on <= ex_date:
                    events.append((delisted_on, 0, sequence, row))
        quantity = 0
        for _, _, _, row in sorted(events, key=lambda item: item[:3]):
            kind = row["t"]
            if kind == "FILL":
                signed = int(row["quantity"]) if row["side"] == "BUY" else -int(row["quantity"])
                quantity += signed
            elif kind == "STOCK_SPLIT":
                updated = Decimal(quantity) * Decimal(row["ratio"])
                if updated != updated.to_integral_value():
                    raise PaperAccountError("ex-date split eligibility creates fractional shares")
                quantity = int(updated)
            else:
                quantity = 0
        if quantity < 0:
            raise PaperAccountError("pre-ex-date paper position cannot be short for cash dividend accounting")
        return quantity

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
                   r.get("fee_schedule_id"), r.get("filled_on"), r.get("execution_evidence"))
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
            evidence = r.get("execution_evidence")
            if evidence is not None and evidence.get("fee_schedule_sha256") != self.fee_schedule.sha256:
                raise PaperAccountError("durable fill execution evidence fee hash does not match its schedule")
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

    def _apply_external_flow(self, r: dict, *, post_to_oms: bool) -> None:
        flow_id, amount = r["flow_id"], Decimal(r["amount"])
        occurred_at = datetime.fromisoformat(r["occurred_at"])
        if (not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", flow_id or "") or not isinstance(r.get("reference"), str)
                or not amount.is_finite() or amount == 0 or occurred_at.tzinfo is None
                or occurred_at.utcoffset() is None or not r["reference"].strip()):
            raise PaperAccountError("durable external flow is invalid")
        occurred_at = occurred_at.astimezone(timezone.utc)
        content = (str(amount), occurred_at.isoformat(), r["reference"])
        if flow_id in self._flow_ids:
            if self._flow_content[flow_id] != content:
                raise PaperAccountError(f"external flow id {flow_id} reused with different content")
            return
        if self._nav_snapshots and occurred_at <= datetime.fromisoformat(self._nav_snapshots[-1]["captured_at"]):
            raise PaperAccountError("late external flow cannot rewrite an already captured NAV date")
        if self._last_flow_at is not None and occurred_at < self._last_flow_at:
            raise PaperAccountError("external flows must be recorded in effective-time order")
        magnitude = Money.of(abs(amount), self.currency)
        if amount > 0:
            entries = (Entry(f"cash:{self.account_id}", DEBIT, magnitude),
                       Entry(f"external_flows:{self.account_id}", CREDIT, magnitude))
        else:
            entries = (Entry(f"external_flows:{self.account_id}", DEBIT, magnitude),
                       Entry(f"cash:{self.account_id}", CREDIT, magnitude))
        self.ledger.post(Transaction(f"external-flow:{flow_id}", entries))
        if post_to_oms:
            self.oms.apply_cash_adjustment(self.account_id, amount, adjustment_id=f"external-flow:{flow_id}")
        self._flow_ids.add(flow_id)
        self._flow_content[flow_id] = content
        self._external_flows.append({"flow_id": flow_id, "amount": str(amount), "occurred_at": occurred_at.isoformat(),
                                     "reference": r["reference"]})
        self._flow_since_snapshot += amount
        self._last_flow_at = occurred_at

    def _apply_nav_snapshot(self, r: dict) -> None:
        session_day = date.fromisoformat(r["session_date"])
        captured_at = datetime.fromisoformat(r["captured_at"])
        nav, external_flow = Decimal(r["nav"]), Decimal(r["external_flow"])
        if (captured_at.tzinfo is None or captured_at.utcoffset() is None
                or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", r["snapshot_id"])
                or not nav.is_finite() or nav <= 0 or not external_flow.is_finite()
                or session_day.isoformat() != r["session_date"]):
            raise PaperAccountError("durable NAV snapshot is invalid")
        capture_utc = captured_at.astimezone(timezone.utc)
        content = (r["session_date"], capture_utc.isoformat(), str(nav), str(external_flow))
        prior = self._nav_snapshot_ids.get(r["snapshot_id"])
        if prior is not None:
            if prior != content:
                raise PaperAccountError(f"NAV snapshot id {r['snapshot_id']} reused with different content")
            return
        if self._nav_snapshots:
            previous = self._nav_snapshots[-1]
            if (session_day.isoformat() <= previous["session_date"]
                    or capture_utc <= datetime.fromisoformat(previous["captured_at"])):
                raise PaperAccountError("NAV snapshots must advance by session date and capture time")
        if (any(marked_at > capture_utc for _, marked_at, _ in self._marks.values())
                or self._last_flow_at is not None and self._last_flow_at > capture_utc):
            raise PaperAccountError("NAV snapshot includes a future mark or external flow")
        if nav != self.equity:
            raise PaperAccountError("NAV snapshot does not match account cash and marked positions")
        if external_flow != self._flow_since_snapshot:
            raise PaperAccountError("NAV snapshot external flow does not reconcile since prior snapshot")
        row = {"snapshot_id": r["snapshot_id"], "session_date": session_day.isoformat(),
               "captured_at": capture_utc.isoformat(), "nav": str(nav), "external_flow": str(external_flow)}
        self._nav_snapshots.append(row)
        self._nav_snapshot_ids[r["snapshot_id"]] = content
        self._flow_since_snapshot = Decimal(0)

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

    def _apply_stock_split(self, r: dict) -> None:
        self._validate_corporate_action(r["action_id"], r["symbol"], date.fromisoformat(r["effective_on"]),
                                        r["source_id"], r["source_sha256"])
        ratio = Decimal(r["ratio"])
        old_qty, basis = self._positions.get(r["symbol"], (0, Decimal(0)))
        new_qty = Decimal(old_qty) * ratio
        if (not ratio.is_finite() or ratio <= 0 or old_qty != int(r["old_quantity"])
                or new_qty != new_qty.to_integral_value() or int(new_qty) != int(r["new_quantity"])):
            raise PaperAccountError("stock split quantity does not reconcile to the prior position")
        content = tuple(sorted((key, value) for key, value in r.items() if key != "t"))
        prior = self._corporate_action_ids.get(r["action_id"])
        if prior is not None:
            if prior != content:
                raise PaperAccountError("corporate action id reused with different split facts")
            return
        self._positions[r["symbol"]] = (int(new_qty), basis)
        self._marks.pop(r["symbol"], None)
        self._corporate_action_ids[r["action_id"]] = content

    def _apply_cash_dividend(self, r: dict) -> None:
        action_id, symbol = r["action_id"], r["symbol"]
        self._validate_corporate_action(action_id, symbol, date.fromisoformat(r["payment_date"]),
                                        r["source_id"], r["source_sha256"])
        gross = Decimal(r["gross_amount"])
        tax = Decimal(r["tax_amount"])
        quantity = int(r["eligible_quantity"])
        per_share, rate = Decimal(r["gross_per_share"]), Decimal(r["withholding_rate"])
        ex_date, payment_date = date.fromisoformat(r["ex_date"]), date.fromisoformat(r["payment_date"])
        if (quantity <= 0 or not per_share.is_finite() or per_share <= 0 or not rate.is_finite()
                or not 0 <= rate <= 1 or payment_date < ex_date or not gross.is_finite() or not tax.is_finite()
                or gross != _round_paise(per_share * quantity) or tax != _round_paise(gross * rate)
                or tax < 0 or tax > gross):
            raise PaperAccountError("durable dividend facts do not reconcile")
        content = tuple(sorted((key, value) for key, value in r.items() if key != "t"))
        prior = self._corporate_action_ids.get(action_id)
        if prior is not None:
            if prior != content:
                raise PaperAccountError("corporate action id reused with different dividend facts")
            return
        net = gross - tax
        entries = []
        if net:
            entries.append(Entry(f"cash:{self.account_id}", DEBIT, Money.of(net, self.currency)))
        if tax:
            entries.append(Entry(f"taxes:{self.account_id}", DEBIT, Money.of(tax, self.currency)))
        entries.append(Entry(f"dividend_income:{self.account_id}", CREDIT, Money.of(gross, self.currency)))
        self.ledger.post(Transaction(f"dividend:{action_id}", tuple(entries)))
        self.oms.apply_cash_adjustment(self.account_id, net, adjustment_id=f"dividend:{action_id}")
        self._corporate_action_ids[action_id] = content

    def _apply_instrument_status(self, r: dict) -> None:
        action_id, symbol = r["action_id"], r["symbol"]
        self._validate_corporate_action(action_id, symbol, date.fromisoformat(r["effective_on"]),
                                        r["source_id"], r["source_sha256"])
        suspended = r["suspended"]
        if type(suspended) is not bool:
            raise PaperAccountError("durable instrument status must be boolean")
        content = tuple(sorted((key, value) for key, value in r.items() if key != "t"))
        prior = self._corporate_action_ids.get(action_id)
        if prior is not None:
            if prior != content:
                raise PaperAccountError("corporate action id reused with different status facts")
            return
        self._instrument_suspended[symbol] = suspended
        self._corporate_action_ids[action_id] = content

    def _apply_delisting_settlement(self, r: dict) -> None:
        action_id, symbol = r["action_id"], r["symbol"]
        self._validate_corporate_action(action_id, symbol, date.fromisoformat(r["effective_on"]),
                                        r["source_id"], r["source_sha256"])
        qty, basis = self._positions.get(symbol, (0, Decimal(0)))
        recovery, proceeds = Decimal(r["recovery_per_share"]), Decimal(r["proceeds"])
        if (qty <= 0 or qty != int(r["quantity"]) or basis != Decimal(r["basis"])
                or not recovery.is_finite() or recovery < 0 or proceeds != _round_paise(recovery * qty)):
            raise PaperAccountError("delisting settlement does not reconcile to the held position")
        content = tuple(sorted((key, value) for key, value in r.items() if key != "t"))
        prior = self._corporate_action_ids.get(action_id)
        if prior is not None:
            if prior != content:
                raise PaperAccountError("corporate action id reused with different settlement facts")
            return
        entries = []
        if proceeds:
            entries.append(Entry(f"cash:{self.account_id}", DEBIT, Money.of(proceeds, self.currency)))
        entries.append(Entry(f"position:{self.account_id}:{symbol}", CREDIT, Money.of(basis, self.currency)))
        difference = proceeds - basis
        if difference > 0:
            entries.append(Entry(f"realized_pnl:{self.account_id}", CREDIT, Money.of(difference, self.currency)))
        elif difference < 0:
            entries.append(Entry(f"realized_pnl:{self.account_id}", DEBIT, Money.of(-difference, self.currency)))
        self.ledger.post(Transaction(f"delisting:{action_id}", tuple(entries)))
        self.oms.apply_cash_adjustment(self.account_id, proceeds, adjustment_id=f"delisting:{action_id}")
        self._positions[symbol] = (0, Decimal(0))
        self._marks.pop(symbol, None)
        self._instrument_suspended[symbol] = True
        self._corporate_action_ids[action_id] = content


def _round_paise(amount: Decimal) -> Decimal:
    if not amount.is_finite():
        raise PaperAccountError("cash amount must be finite")
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

