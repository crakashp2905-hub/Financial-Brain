from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from financial_brain.forward_edge.exposure_limits import (
    ExposureIntent,
    ExposureLimits,
    ExposureReservationBook,
)
from financial_brain.kernel.oms import Oms
from financial_brain.portfolio.capital_allocator import (
    AllocationRequest,
    CapitalAllocationError,
    CapitalAllocator,
)


def _request(identity: str, amount: Decimal = Decimal("600"), *, desk: str = "INTRADAY"):
    instrument = f"NSE:{identity}"
    exposure = ExposureIntent(identity, instrument, "IT", "BUY", float(amount),
                              capital_required=float(amount), worst_case_loss=float(amount) / 10)
    return AllocationRequest(identity, "paper", desk, "strategy-v1", instrument, "BUY", 1,
                             amount, amount, exposure)


def _allocator(*, cash="1000", exposure_cap=1000, gates=()):
    oms = Oms({"paper": Decimal(cash)})
    book = ExposureReservationBook(ExposureLimits(
        exposure_cap, exposure_cap, exposure_cap, {"IT": exposure_cap},
        {"market": exposure_cap}, max_allocated_capital=exposure_cap,
        max_worst_case_loss=exposure_cap,
    ))
    return CapitalAllocator(oms=oms, account="paper", exposure_book=book,
                            policy_gates=gates), oms, book


def test_shared_cash_and_exposure_reservations_prevent_cross_desk_double_spend():
    allocator, oms, book = _allocator(cash="1000", exposure_cap=1000)
    requests = (_request("a", desk="SWING"), _request("b", desk="OPTIONS"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(allocator.reserve, requests))
    assert sum(item.granted for item in receipts) == 1
    assert oms.available("paper") == Decimal("400")
    assert book.snapshot()["allocated_capital"] == 600


def test_request_replay_is_idempotent_and_conflicting_reuse_is_rejected():
    allocator, oms, book = _allocator()
    request = _request("a")
    first = allocator.reserve(request)
    assert allocator.reserve(request) == first
    assert oms.reserved("paper") == Decimal("600")
    assert book.snapshot()["pending_intents"] == ["a"]
    with pytest.raises(CapitalAllocationError, match="changed request"):
        allocator.reserve(_request("a", Decimal("500")))


def test_exposure_denial_rolls_back_prior_cash_reservation():
    allocator, oms, book = _allocator(cash="1000", exposure_cap=500)
    result = allocator.reserve(_request("a", Decimal("600")))
    assert not result.granted
    assert "exposure reservation denied" in result.reason
    assert oms.available("paper") == Decimal("1000")
    assert oms.reserved("paper") == Decimal(0)
    assert book.snapshot()["pending_intents"] == []


def test_every_policy_gate_must_explicitly_pass():
    allocator, oms, _ = _allocator(gates=(lambda _: True, lambda _: None))
    result = allocator.reserve(_request("a", Decimal("100")))
    assert not result.granted
    assert "denied or abstained" in result.reason
    assert oms.reserved("paper") == Decimal(0)


def test_gate_exception_fails_closed_without_reserving():
    def broken(_):
        raise RuntimeError("unavailable")

    allocator, oms, _ = _allocator(gates=(broken,))
    result = allocator.reserve(_request("a", Decimal("100")))
    assert not result.granted
    assert "policy gate unavailable" in result.reason
    assert oms.reserved("paper") == Decimal(0)


def test_unsubmitted_allocation_can_be_released_once():
    allocator, oms, book = _allocator()
    assert allocator.reserve(_request("a", Decimal("100"))).granted
    released = allocator.release("a")
    assert not released.granted
    assert oms.available("paper") == Decimal("1000")
    assert book.snapshot()["pending_intents"] == []
    with pytest.raises(CapitalAllocationError, match="unknown granted"):
        allocator.release("a")
