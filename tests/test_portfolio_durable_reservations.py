from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal

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
        "SETTLEMENT_CASH": Decimal("50000"),
        "INSTRUMENT_QUANTITY:NSE:ABC": Decimal("100"),
        "SELLABLE_INVENTORY:NSE:ABC": Decimal("50"),
    }
    default.update(limits)
    return ReservationPolicy("paper-account", "v1", FROZEN, default, SOURCE)


def _request(reservation_id, **amounts):
    values = {key: Decimal(str(value)) for key, value in amounts.items()}
    return ReservationRequest(reservation_id, "paper-account", "intraday", NOW, values, "b" * 64)


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


def test_failed_position_transition_rolls_back_fill_and_binding_receipt(tmp_path):
    path = tmp_path / "atomic-rejection.sqlite"
    reservations = DurableReservationLedger(path)
    reservations.reserve(
        _policy(), _request("s::sell", **{"SELLABLE_INVENTORY:NSE:ABC": "5"}))
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
