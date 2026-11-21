from concurrent.futures import ThreadPoolExecutor

import pytest

from financial_brain.forward_edge.exposure_limits import (
    ExposureIntent,
    ExposureLimitError,
    ExposureLimits,
    ExposureReservationBook,
)


def _book(gross=100_000, net=100_000, name=100_000, sector=100_000, factor=100_000):
    return ExposureReservationBook(ExposureLimits(gross, net, name, {"IT": sector}, {"market": factor}))


def _intent(identity, amount=20_000, *, instrument=None, side="BUY", factor=0,
            capital=0, worst_loss=0):
    return ExposureIntent(identity, instrument or f"NSE:{identity}", "IT", side, amount,
                          {"market": factor} if factor else {}, capital, worst_loss)


def test_concurrent_intents_cannot_race_past_portfolio_gross_cap() -> None:
    book = _book()
    intents = tuple(_intent(f"i{i}") for i in range(10))
    def attempt(item):
        try:
            return book.reserve(item)
        except ExposureLimitError:
            return False
    with ThreadPoolExecutor(max_workers=10) as pool:
        accepted = list(pool.map(attempt, intents))
    assert sum(accepted) == 5
    assert book.snapshot()["gross"] == 100_000


def test_partial_fills_convert_reservation_and_fill_replay_is_idempotent() -> None:
    book = _book()
    intent = _intent("order-1", amount=80_000, instrument="NSE:ABC", factor=40_000)
    assert book.reserve(intent)
    assert book.record_fill("order-1", "fill-1", 30_000)
    assert not book.record_fill("order-1", "fill-1", 30_000)
    assert book.snapshot()["gross"] == 80_000
    assert book.cancel_remaining("order-1") == 50_000
    assert book.snapshot()["gross"] == 30_000
    with pytest.raises(ExposureLimitError, match="reused"):
        book.record_fill("order-1", "fill-1", 25_000)


def test_name_sector_net_and_factor_caps_bind_new_reservations() -> None:
    name_book = _book(name=30_000)
    name_book.reserve(_intent("a", 20_000, instrument="ABC"))
    with pytest.raises(ExposureLimitError, match="single-name"):
        name_book.reserve(_intent("b", 20_000, instrument="ABC"))

    sector_book = _book(sector=30_000)
    sector_book.reserve(_intent("a", 20_000))
    with pytest.raises(ExposureLimitError, match="sector gross"):
        sector_book.reserve(_intent("b", 20_000))

    factor_book = _book(factor=30_000)
    factor_book.reserve(_intent("a", 20_000, factor=20_000))
    with pytest.raises(ExposureLimitError, match="factor exposure"):
        factor_book.reserve(_intent("b", 20_000, factor=20_000))


def test_opposite_side_gross_is_not_erased_by_netting_and_ids_are_immutable() -> None:
    book = _book(gross=30_000, net=100_000, name=100_000, sector=100_000)
    book.reserve(_intent("long", 20_000))
    with pytest.raises(ExposureLimitError, match="gross"):
        book.reserve(_intent("short", 20_000, side="SELL"))
    with pytest.raises(ExposureLimitError, match="changed exposure"):
        book.reserve(_intent("long", 10_000))


def test_pending_orders_reserve_cash_margin_and_defined_risk_loss_once() -> None:
    book = ExposureReservationBook(ExposureLimits(500_000, 500_000, 500_000,
                                                   {"IT": 500_000}, {"market": 500_000},
                                                   max_allocated_capital=100_000,
                                                   max_worst_case_loss=50_000))
    first = _intent("option-spread", 50_000, capital=80_000, worst_loss=40_000)
    book.reserve(first)
    with pytest.raises(ExposureLimitError, match="double allocated"):
        book.reserve(_intent("cash-stock", 30_000, capital=30_000, worst_loss=1_000))
    with pytest.raises(ExposureLimitError, match="worst-case loss"):
        book.reserve(_intent("second-spread", 20_000, capital=10_000, worst_loss=20_000))
    assert book.snapshot()["allocated_capital"] == 80_000
    assert book.snapshot()["worst_case_loss"] == 40_000

    book.record_fill("option-spread", "spread-fill", 25_000)
    assert book.snapshot()["allocated_capital"] == 80_000  # half utilized, half still reserved
    assert book.snapshot()["worst_case_loss"] == 40_000
    book.cancel_remaining("option-spread")
    assert book.snapshot()["allocated_capital"] == 40_000
    assert book.snapshot()["worst_case_loss"] == 20_000
    book.reserve(_intent("cash-stock", 30_000, capital=60_000, worst_loss=1_000))
    assert book.snapshot()["allocated_capital"] == 100_000
