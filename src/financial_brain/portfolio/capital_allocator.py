"""Fail-closed shared paper-capital reservations across desks.

This coordinates existing cash and exposure guards before a paper order is created.
It is deliberately in-process: durable cross-asset settlement and restart recovery
remain responsibilities of the unified ledger integration.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import threading
from typing import Callable, Iterable

from financial_brain.forward_edge.exposure_limits import (
    ExposureIntent,
    ExposureLimitError,
    ExposureReservationBook,
)
from financial_brain.kernel.oms import InsufficientCash, Oms, OmsError


class CapitalAllocationError(PermissionError):
    """A reservation is invalid, unavailable, or denied by a configured guard."""


@dataclass(frozen=True, slots=True)
class AllocationRequest:
    allocation_id: str
    account: str
    desk: str
    strategy_id: str
    instrument_id: str
    side: str
    quantity: int
    limit_price: Decimal
    cash_reserve: Decimal
    exposure: ExposureIntent

    def __post_init__(self) -> None:
        if not all(value.strip() for value in
                   (self.allocation_id, self.account, self.desk, self.strategy_id,
                    self.instrument_id)):
            raise CapitalAllocationError("allocation, account, desk, strategy and instrument are required")
        if self.side not in {"BUY", "SELL"} or type(self.quantity) is not int or self.quantity <= 0:
            raise CapitalAllocationError("side and positive integer quantity are required")
        if not self.limit_price.is_finite() or self.limit_price <= 0:
            raise CapitalAllocationError("limit price must be positive and finite")
        if not self.cash_reserve.is_finite() or self.cash_reserve < 0:
            raise CapitalAllocationError("cash reserve must be finite and non-negative")
        if self.exposure.intent_id != self.allocation_id:
            raise CapitalAllocationError("exposure intent ID must match allocation ID")
        if self.exposure.instrument_id != self.instrument_id or self.exposure.side != self.side:
            raise CapitalAllocationError("exposure instrument and side must match allocation")


@dataclass(frozen=True, slots=True)
class AllocationReceipt:
    allocation_id: str
    granted: bool
    cash_reserved: Decimal
    exposure_reserved: float
    reason: str | None = None


class CapitalAllocator:
    """Reserve cash and risk atomically against one shared in-process paper book.

    Every configured policy callback must return exactly ``True``. Exceptions,
    missing policies, and false answers fail closed. A later reservation failure
    rolls back the earlier reservation before returning a denial.
    """

    def __init__(self, *, oms: Oms, account: str,
                 exposure_book: ExposureReservationBook,
                 policy_gates: Iterable[Callable[[AllocationRequest], bool]] = ()) -> None:
        if not account.strip():
            raise CapitalAllocationError("shared account is required")
        self._oms = oms
        self._account = account
        self._exposure = exposure_book
        self._gates = tuple(policy_gates)
        self._lock = threading.RLock()
        self._receipts: dict[str, tuple[AllocationRequest, AllocationReceipt]] = {}

    def reserve(self, request: AllocationRequest) -> AllocationReceipt:
        with self._lock:
            prior = self._receipts.get(request.allocation_id)
            if prior:
                if prior[0] != request:
                    raise CapitalAllocationError("allocation ID reused with changed request")
                return prior[1]
            if request.account != self._account:
                return self._deny(request, "request is outside the configured shared account")
            for gate in self._gates:
                try:
                    passed = gate(request)
                except Exception as exc:
                    return self._deny(request, f"policy gate unavailable: {type(exc).__name__}")
                if passed is not True:
                    return self._deny(request, "a configured allocator policy denied or abstained")

            order_id = f"allocation:{request.allocation_id}"
            try:
                order = self._oms.submit_intent(
                    client_id=order_id,
                    account=self._account,
                    side=request.side,
                    quantity=request.quantity,
                    limit=request.limit_price,
                    reserve=request.cash_reserve,
                )
            except (OmsError, InsufficientCash) as exc:
                return self._deny(request, f"shared cash reservation denied: {type(exc).__name__}")
            try:
                self._exposure.reserve(request.exposure)
            except ExposureLimitError as exc:
                # The intent is still in CREATED, so rejecting it releases the OMS hold.
                self._oms.move(order.client_id, self._rejected_state())
                return self._deny(request, f"portfolio exposure reservation denied: {exc}")
            receipt = AllocationReceipt(request.allocation_id, True, request.cash_reserve,
                                        request.exposure.notional)
            self._receipts[request.allocation_id] = (request, receipt)
            return receipt

    def release(self, allocation_id: str) -> AllocationReceipt:
        """Release an unsubmitted reservation; submitted/fill lifecycle uses OMS reconciliation."""
        with self._lock:
            prior = self._receipts.get(allocation_id)
            if prior is None or not prior[1].granted:
                raise CapitalAllocationError("unknown granted allocation")
            request, receipt = prior
            order = self._oms.order(f"allocation:{allocation_id}")
            if order.state.value != "CREATED":
                raise CapitalAllocationError("only an unsubmitted paper allocation may be released here")
            self._exposure.cancel_remaining(allocation_id)
            self._oms.move(order.client_id, self._rejected_state())
            released = AllocationReceipt(allocation_id, False, receipt.cash_reserved,
                                         receipt.exposure_reserved, "released before submission")
            self._receipts[allocation_id] = (request, released)
            return released

    @staticmethod
    def _rejected_state():
        # Importing State at module scope would be equally safe; keep the OMS enum explicit.
        from financial_brain.kernel.oms import State
        return State.REJECTED

    def _deny(self, request: AllocationRequest, reason: str) -> AllocationReceipt:
        receipt = AllocationReceipt(request.allocation_id, False, Decimal(0), 0.0, reason)
        self._receipts[request.allocation_id] = (request, receipt)
        return receipt
