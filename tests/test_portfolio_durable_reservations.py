from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import random

import pytest

from financial_brain.portfolio.durable_reservations import (
    DurableReservationLedger,
    ReservationDisposition,
    ReservationError,
    ReservationPolicy,
    ReservationRequest,
)
from financial_brain.portfolio.canonical_ledger import (
    AssetClass,
    CanonicalPortfolioLedger,
    EventKind,
    PortfolioEvent,
)


IST = timezone(timedelta(hours=5, minutes=30))
FROZEN = datetime(2026, 11, 25, 9, 0, tzinfo=IST)
NOW = datetime(2026, 11, 25, 9, 1, tzinfo=IST)
SOURCE = "a" * 64


def _policy(**limits):
    default = {
        "CASH": Decimal("100000"),
        "MARGIN": Decimal("50000"),
        "RISK_CAPITAL": Decimal("10000"),
        "OPTION_BUYING_POWER": Decimal("20000"),
        "PAPER_COST_BUFFER": Decimal("10000"),
        "SETTLEMENT_CASH": Decimal("50000"),
        "INSTRUMENT_QUANTITY:NSE:ABC": Decimal("100"),
        "SELLABLE_INVENTORY:NSE:ABC": Decimal("50"),
        "INSTRUMENT_QUANTITY:NFO:NIFTY-FUT": Decimal("10"),
    }
    default.update(limits)
    return ReservationPolicy("paper-account", "v1", FROZEN, default, SOURCE)


def _request(reservation_id, **amounts):
    values = {key: Decimal(str(value)) for key, value in amounts.items()}
    return ReservationRequest(reservation_id, "paper-account", "intraday", NOW, values, "b" * 64)


def _equity_fill(event_id, quantity, *, price="100", fee_amount="0"):
    fill = PortfolioEvent(
        account_id="paper-account", sleeve_id="intraday", source_system="FORWARD_PAPER",
        source_event_id=event_id, source_sha256="c" * 64, event_kind=EventKind.FILL,
        occurred_at="2026-11-25T09:02:00+05:30", asset_class=AssetClass.EQUITY,
        instrument_id="NSE:ABC", side="BUY", position_action="BUY", quantity=str(quantity),
        price=str(price), multiplier="1",
    )
    fee = None
    if Decimal(fee_amount) > 0:
        fee = PortfolioEvent(
            account_id="paper-account", sleeve_id="intraday", source_system="FORWARD_PAPER",
            source_event_id=f"{event_id}:fee", source_sha256="c" * 64, event_kind=EventKind.FEE,
            occurred_at="2026-11-25T09:02:00+05:30", cash_delta=f"-{fee_amount}",
            metadata={"fill_event_id": event_id},
        )
    return fill, fee


def test_atomic_reservation_covers_multiple_resource_caps_and_replay(tmp_path):
    ledger = DurableReservationLedger(tmp_path / "reservations.sqlite")
    request = _request("order-1", CASH="1000", RISK_CAPITAL="300",
                       **{"INSTRUMENT_QUANTITY:NSE:ABC": "10"})
    result = ledger.reserve(_policy(), request)
    assert result.disposition is ReservationDisposition.RESERVED
    assert result.active_amounts["CASH"] == Decimal("1000")
    replay = ledger.reserve(_policy(), request)
    assert replay.disposition is ReservationDisposition.REPLAYED
    assert replay.event_sha256 == result.event_sha256
    assert ledger.active_amounts("paper-account")["RISK_CAPITAL"] == Decimal("300")
    assert ledger.verify("paper-account") == {
        "events": 1, "mismatched": 0, "reservations_missing_receipts": 0,
        "released_reservations_missing_receipts": 0,
    }


def test_any_resource_overage_rolls_back_all_resources(tmp_path):
    ledger = DurableReservationLedger(tmp_path / "reservations.sqlite")
    policy = _policy(CASH=Decimal("100"), RISK_CAPITAL=Decimal("10"))
    with pytest.raises(ReservationError, match="budget exceeded"):
        ledger.reserve(policy, _request("too-large", CASH="50", RISK_CAPITAL="11"))
    assert ledger.active_amounts("paper-account") == {}
    assert ledger.verify("paper-account")["events"] == 0


def test_concurrent_desks_cannot_overreserve_account_cash(tmp_path):
    path = tmp_path / "shared-reservations.sqlite"
    policy = _policy(CASH=Decimal("100"))
    requests = (_request("desk-a", CASH="60"), _request("desk-b", CASH="60"))

    def reserve(request):
        try:
            return DurableReservationLedger(path).reserve(policy, request).disposition
        except ReservationError:
            return "DENIED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, requests))
    assert results.count(ReservationDisposition.RESERVED) == 1
    assert results.count("DENIED") == 1
    active = DurableReservationLedger(path).active_amounts("paper-account")
    assert active["CASH"] == Decimal("60")


def test_thousand_intent_stress_never_exceeds_cash_margin_or_risk_caps(tmp_path):
    ledger = DurableReservationLedger(tmp_path / "thousand-intents.sqlite")
    limits = {"CASH": Decimal("5000"), "MARGIN": Decimal("2000"),
              "RISK_CAPITAL": Decimal("1000")}
    policy = _policy(**limits)
    rng = random.Random(13015)
    accepted = rejected = 0
    for index in range(1_000):
        amounts = {resource: Decimal(rng.randint(1, 75)) for resource in limits}
        try:
            ledger.reserve(policy, _request(f"stress-{index:04d}", **amounts))
            accepted += 1
        except ReservationError as exc:
            assert "budget exceeded" in str(exc)
            rejected += 1
        active = ledger.active_amounts("paper-account")
        assert all(active.get(resource, Decimal(0)) <= cap for resource, cap in limits.items())
    assert accepted > 0 and rejected > 0
    assert ledger.verify("paper-account")["mismatched"] == 0


def test_release_requires_source_bound_terminal_event_and_is_replay_safe(tmp_path):
    ledger = DurableReservationLedger(tmp_path / "reservations.sqlite")
    ledger.reserve(_policy(), _request("order-1", CASH="1000"))
    with pytest.raises(ReservationError, match="terminal order state"):
        ledger.release(account_id="paper-account", reservation_id="order-1",
                       terminal_event_id="state-1", terminal_kind="OPEN",
                       occurred_at=NOW, source_sha256="c" * 64)
    assert ledger.release(account_id="paper-account", reservation_id="order-1",
                          terminal_event_id="state-1", terminal_kind="CANCELLED",
                          occurred_at=NOW + timedelta(minutes=1), source_sha256="c" * 64) \
        is ReservationDisposition.RELEASED
    assert ledger.release(account_id="paper-account", reservation_id="order-1",
                          terminal_event_id="state-1", terminal_kind="CANCELLED",
                          occurred_at=NOW + timedelta(minutes=1), source_sha256="c" * 64) \
        is ReservationDisposition.ALREADY_RELEASED
    with pytest.raises(ReservationError, match="conflicting content"):
        ledger.release(account_id="paper-account", reservation_id="order-1",
                       terminal_event_id="state-1", terminal_kind="FILLED",
                       occurred_at=NOW + timedelta(minutes=1), source_sha256="c" * 64)
    assert ledger.active_amounts("paper-account") == {}
    assert ledger.verify("paper-account") == {
        "events": 2, "mismatched": 0, "reservations_missing_receipts": 0,
        "released_reservations_missing_receipts": 0,
    }


def test_conflicting_replay_policy_change_and_naive_time_fail_closed(tmp_path):
    ledger = DurableReservationLedger(tmp_path / "reservations.sqlite")
    policy = _policy()
    ledger.reserve(policy, _request("order-1", CASH="1000"))
    with pytest.raises(ReservationError, match="different request content"):
        ledger.reserve(policy, _request("order-1", CASH="2000"))
    with pytest.raises(ReservationError, match="policy changed"):
        ledger.reserve(_policy(CASH=Decimal("90000")), _request("order-2", CASH="1000"))
    with pytest.raises(ReservationError, match="timezone-aware"):
        ReservationRequest("bad", "paper-account", "intraday", NOW.replace(tzinfo=None),
                           {"CASH": Decimal("1")}, "b" * 64)


def test_paper_fill_and_reservation_receipt_commit_atomically_and_replay_once(tmp_path):
    path = tmp_path / "shared-book.sqlite"
    reservations = DurableReservationLedger(path)
    reservations.reserve(
        _policy(), _request("s::o", CASH="1000", **{"INSTRUMENT_QUANTITY:NSE:ABC": "10"}))
    fill = PortfolioEvent(
        account_id="paper-account", sleeve_id="intraday", source_system="FORWARD_PAPER",
        source_event_id="s:o:fill", source_sha256="c" * 64, event_kind=EventKind.FILL,
        occurred_at="2026-11-25T09:02:00+05:30", asset_class=AssetClass.EQUITY,
        instrument_id="NSE:ABC", side="BUY", position_action="BUY", quantity="5", price="100",
        metadata={"reservation_id": "s::o"},
    )

    payload_sha = reservations.record_paper_fill(reservation_id="s::o", fill=fill)
    replay_sha = reservations.record_paper_fill(reservation_id="s::o", fill=fill)

    assert replay_sha == payload_sha
    canonical = CanonicalPortfolioLedger(path)
    assert canonical.snapshot("paper-account")["positions"][0]["quantity"] == Decimal("5")
    assert canonical.event_count() == 1
    assert reservations.active_amounts("paper-account")["CASH"] == Decimal("1000")
    assert reservations.verify("paper-account") == {
        "events": 2, "mismatched": 0, "reservations_missing_receipts": 0,
        "released_reservations_missing_receipts": 0,
    }


@pytest.mark.parametrize(
    ("limits", "first", "second", "message"),
    [
        ({"CASH": "1000", "INSTRUMENT_QUANTITY:NSE:ABC": "5"},
         ("fill-1", "3", "100", "0"), ("fill-2", "3", "100", "0"), "quantity exceeds"),
        ({"CASH": "300", "PAPER_COST_BUFFER": "10", "INSTRUMENT_QUANTITY:NSE:ABC": "5"},
         ("fill-1", "2", "100", "1"), ("fill-2", "2", "100", "1"), "cash reservation"),
        ({"CASH": "1000", "PAPER_COST_BUFFER": "1.50",
          "INSTRUMENT_QUANTITY:NSE:ABC": "5"},
         ("fill-1", "1", "100", "1"), ("fill-2", "1", "100", "1"), "cost-buffer reservation"),
    ],
)
def test_partial_fills_cannot_cumulatively_exceed_order_reservation(
        tmp_path, limits, first, second, message):
    path = tmp_path / "partial-fill-reservation.sqlite"
    reservations = DurableReservationLedger(path)
    reservations.reserve(_policy(**{key: Decimal(value) for key, value in limits.items()}),
                         _request("partial-order", **limits))
    first_fill, first_fee = _equity_fill(first[0], first[1], price=first[2], fee_amount=first[3])
    reservations.record_paper_fill(reservation_id="partial-order", fill=first_fill, fee=first_fee)
    second_fill, second_fee = _equity_fill(second[0], second[1], price=second[2], fee_amount=second[3])

    with pytest.raises(ReservationError, match=message):
        reservations.record_paper_fill(reservation_id="partial-order", fill=second_fill, fee=second_fee)

    snapshot = CanonicalPortfolioLedger(path).snapshot("paper-account")
    assert snapshot["positions"][0]["quantity"] == Decimal(first[1])
    assert reservations.verify("paper-account")["mismatched"] == 0


def test_paper_fill_and_source_bound_fee_commit_atomically_and_replay_once(tmp_path):
    path = tmp_path / "shared-fee-book.sqlite"
    reservations = DurableReservationLedger(path)
    reservations.reserve(
        _policy(), _request("s::o", CASH="600", PAPER_COST_BUFFER="2.50",
                            **{"INSTRUMENT_QUANTITY:NSE:ABC": "10"}))
    fill = PortfolioEvent(
        account_id="paper-account", sleeve_id="intraday", source_system="FORWARD_PAPER",
        source_event_id="s:o:fill", source_sha256="c" * 64, event_kind=EventKind.FILL,
        occurred_at="2026-11-25T09:02:00+05:30", asset_class=AssetClass.EQUITY,
        instrument_id="NSE:ABC", side="BUY", position_action="BUY", quantity="5", price="100",
    )
    fee = PortfolioEvent(
        account_id="paper-account", sleeve_id="intraday", source_system="FORWARD_PAPER",
        source_event_id="s:o:fees", source_sha256="c" * 64, event_kind=EventKind.FEE,
        occurred_at="2026-11-25T09:02:00+05:30", cash_delta="-2.50",
        metadata={"fill_event_id": "s:o:fill"},
    )

    first = reservations.record_paper_fill(reservation_id="s::o", fill=fill, fee=fee)
    replay = reservations.record_paper_fill(reservation_id="s::o", fill=fill, fee=fee)

    canonical = CanonicalPortfolioLedger(path)
    assert replay == first
    assert canonical.event_count() == 2
    assert canonical.snapshot("paper-account")["cash"]["INR"] == Decimal("-502.50")
    assert reservations.verify("paper-account")["mismatched"] == 0


def test_paper_fill_rejects_fee_above_frozen_cost_buffer(tmp_path):
    path = tmp_path / "fee-buffer-overrun.sqlite"
    reservations = DurableReservationLedger(path)
    reservations.reserve(
        _policy(), _request("s::o", CASH="600", PAPER_COST_BUFFER="2.49",
                            **{"INSTRUMENT_QUANTITY:NSE:ABC": "10"}))
    fill = PortfolioEvent(
        account_id="paper-account", sleeve_id="intraday", source_system="FORWARD_PAPER",
        source_event_id="s:o:fill", source_sha256="c" * 64, event_kind=EventKind.FILL,
        occurred_at="2026-11-25T09:02:00+05:30", asset_class=AssetClass.EQUITY,
        instrument_id="NSE:ABC", side="BUY", position_action="BUY", quantity="5", price="100",
    )
    fee = PortfolioEvent(
        account_id="paper-account", sleeve_id="intraday", source_system="FORWARD_PAPER",
        source_event_id="s:o:fees", source_sha256="c" * 64, event_kind=EventKind.FEE,
        occurred_at="2026-11-25T09:02:00+05:30", cash_delta="-2.50",
        metadata={"fill_event_id": "s:o:fill"},
    )

    with pytest.raises(ReservationError, match="cost-buffer reservation"):
        reservations.record_paper_fill(reservation_id="s::o", fill=fill, fee=fee)

    assert CanonicalPortfolioLedger(path).event_count() == 0


def test_paper_fee_must_match_fill_source_and_rolls_back_on_conflict(tmp_path):
    path = tmp_path / "mismatched-fee-book.sqlite"
    reservations = DurableReservationLedger(path)
    reservations.reserve(
        _policy(), _request("s::o", CASH="600", **{"INSTRUMENT_QUANTITY:NSE:ABC": "10"}))
    fill = PortfolioEvent(
        account_id="paper-account", sleeve_id="intraday", source_system="FORWARD_PAPER",
        source_event_id="s:o:fill", source_sha256="c" * 64, event_kind=EventKind.FILL,
        occurred_at="2026-11-25T09:02:00+05:30", asset_class=AssetClass.EQUITY,
        instrument_id="NSE:ABC", side="BUY", position_action="BUY", quantity="5", price="100",
    )
    bad_fee = PortfolioEvent(
        account_id="paper-account", sleeve_id="intraday", source_system="FORWARD_PAPER",
        source_event_id="s:o:fees", source_sha256="c" * 64, event_kind=EventKind.FEE,
        occurred_at="2026-11-25T09:02:00+05:30", cash_delta="-2.50",
        metadata={"fill_event_id": "different-fill"},
    )

    with pytest.raises(ReservationError, match="source-bound"):
        reservations.record_paper_fill(reservation_id="s::o", fill=fill, fee=bad_fee)

    assert CanonicalPortfolioLedger(path).event_count() == 0
    assert reservations.verify("paper-account")["events"] == 1


def test_failed_position_transition_rolls_back_fill_and_binding_receipt(tmp_path):
    path = tmp_path / "atomic-rejection.sqlite"
    reservations = DurableReservationLedger(path)
    reservations.reserve(
        _policy(), _request("s::sell", **{"SELLABLE_INVENTORY:NSE:ABC": "5",
                                          "INSTRUMENT_QUANTITY:NSE:ABC": "5"}))
    invalid_close = PortfolioEvent(
        account_id="paper-account", sleeve_id="intraday", source_system="FORWARD_PAPER",
        source_event_id="s:sell:fill", source_sha256="d" * 64, event_kind=EventKind.FILL,
        occurred_at="2026-11-25T09:02:00+05:30", asset_class=AssetClass.EQUITY,
        instrument_id="NSE:ABC", side="SELL", position_action="SELL", quantity="5", price="100",
    )

    with pytest.raises(ReservationError, match="canonical position transition denied"):
        reservations.record_paper_fill(reservation_id="s::sell", fill=invalid_close)

    assert CanonicalPortfolioLedger(path).event_count() == 0
    assert reservations.active_amounts("paper-account")["SELLABLE_INVENTORY:NSE:ABC"] == Decimal("5")
    assert reservations.verify("paper-account")["events"] == 1


@pytest.mark.parametrize(
    ("reserved_amounts", "fill_quantity", "fill_price", "asset_class", "multiplier", "message"),
    [
        ({"INSTRUMENT_QUANTITY:NSE:ABC": "4", "CASH": "1000"}, "5", "100", AssetClass.EQUITY,
         "1", "quantity exceeds"),
        ({"INSTRUMENT_QUANTITY:NSE:ABC": "5", "CASH": "499"}, "5", "100", AssetClass.EQUITY,
         "1", "cash reservation"),
        ({"INSTRUMENT_QUANTITY:NSE:ABC": "5", "CASH": "500", "OPTION_BUYING_POWER": "499"},
         "5", "100", AssetClass.OPTION, "1", "options-buying-power"),
    ],
)
def test_paper_fill_cannot_exceed_reserved_quantity_cash_or_option_buying_power(
        tmp_path, reserved_amounts, fill_quantity, fill_price, asset_class, multiplier, message):
    path = tmp_path / f"underreserved-{asset_class.value}.sqlite"
    reservations = DurableReservationLedger(path)
    reservations.reserve(_policy(), _request("s::o", **reserved_amounts))
    fill = PortfolioEvent(
        account_id="paper-account", sleeve_id="intraday", source_system="FORWARD_PAPER",
        source_event_id="s:o:fill", source_sha256="e" * 64, event_kind=EventKind.FILL,
        occurred_at="2026-11-25T09:02:00+05:30", asset_class=asset_class,
        instrument_id="NSE:ABC", side="BUY", position_action="BUY", quantity=fill_quantity,
        price=fill_price, multiplier=multiplier,
    )

    with pytest.raises(ReservationError, match=message):
        reservations.record_paper_fill(reservation_id="s::o", fill=fill)

    assert CanonicalPortfolioLedger(path).event_count() == 0
    assert reservations.verify("paper-account")["events"] == 1


def test_futures_fill_requires_frozen_margin_and_risk_but_not_full_notional_cash(tmp_path):
    path = tmp_path / "futures-reservation.sqlite"
    reservations = DurableReservationLedger(path)
    reservations.reserve(_policy(), _request(
        "s::future", MARGIN="50000", RISK_CAPITAL="10000",
        **{"INSTRUMENT_QUANTITY:NFO:NIFTY-FUT": "2"}))
    fill = PortfolioEvent(
        account_id="paper-account", sleeve_id="intraday", source_system="FORWARD_PAPER",
        source_event_id="s:future:fill", source_sha256="f" * 64, event_kind=EventKind.FILL,
        occurred_at="2026-11-25T09:02:00+05:30", asset_class=AssetClass.FUTURE,
        instrument_id="NFO:NIFTY-FUT", side="BUY", position_action="BUY",
        quantity="1", price="22000", multiplier="25",
    )

    reservations.record_paper_fill(reservation_id="s::future", fill=fill)

    snapshot = CanonicalPortfolioLedger(path).snapshot("paper-account")
    assert snapshot["positions"][0]["quantity"] == Decimal("1")
    assert snapshot["cash"] == {}
