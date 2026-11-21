from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from financial_brain.paper.mf_lot_book import (
    MfPaperBookError,
    account_snapshot,
    create_account,
    redeem,
    subscribe,
)
from financial_brain.providers.mf_scheme_terms import ExitLoadBand
from financial_brain.storage.db import Database
from financial_brain.config import Config

HASH_A = "a" * 64
HASH_B = "b" * 64
NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


@pytest.fixture
def connection(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    with db.connect() as con:
        yield con


def _subscribe(con, *, event_id, lot_id, amount, schedule, digest):
    return subscribe(
        con,
        account_id="paper-1",
        event_id=event_id,
        lot_id=lot_id,
        scheme_code="100001",
        isin="INF000000001",
        allotted_on=date(2026, 1, 1),
        investment_inr=amount,
        nav_per_unit=100,
        exit_loads=schedule,
        exit_loads_sha256=digest,
        now=NOW,
    )


def test_partial_redemption_reconciles_fifo_lot_loads_cash_and_idempotency(connection):
    con = connection
    create_account(con, "paper-1", 5000, now=NOW)
    first_schedule = (ExitLoadBand(0, Decimal("1")), ExitLoadBand(91, Decimal("0")))
    second_schedule = (ExitLoadBand(0, Decimal("2")),)
    _subscribe(
        con,
        event_id="sub-1",
        lot_id="lot-1",
        amount=1000,
        schedule=first_schedule,
        digest=HASH_A,
    )
    _subscribe(
        con,
        event_id="sub-2",
        lot_id="lot-2",
        amount=500,
        schedule=second_schedule,
        digest=HASH_B,
    )

    result = redeem(
        con,
        account_id="paper-1",
        event_id="red-1",
        scheme_code="100001",
        isin="INF000000001",
        redemption_date=date(2026, 1, 15),
        redemption_units=12,
        nav_per_unit=100,
        now=NOW,
    )

    assert result["gross_inr"] == Decimal("1200")
    assert result["exit_load_inr"] == Decimal("14.00")
    assert result["net_inr"] == Decimal("1186.00")
    assert [
        (a.lot_id, a.units_redeemed, a.units_remaining) for a in result["allocations"]
    ] == [
        ("lot-1", Decimal("10"), Decimal("0")),
        ("lot-2", Decimal("2"), Decimal("3")),
    ]
    assert con.execute(
        "SELECT cash_inr FROM mf_paper_accounts WHERE account_id='paper-1'"
    ).fetchone()[0] == Decimal("4686")
    assert (
        con.execute(
            "SELECT units_remaining FROM mf_paper_lots "
            "WHERE account_id='paper-1' AND lot_id='lot-2'"
        ).fetchone()[0]
        == 3
    )

    replay = redeem(
        con,
        account_id="paper-1",
        event_id="red-1",
        scheme_code="100001",
        isin="INF000000001",
        redemption_date=date(2026, 1, 15),
        redemption_units=12,
        nav_per_unit=100,
        now=NOW,
    )
    assert replay["status"] == "already_applied"
    assert con.execute(
        "SELECT cash_inr FROM mf_paper_accounts WHERE account_id='paper-1'"
    ).fetchone()[0] == Decimal("4686")
    snapshot = account_snapshot(con, "paper-1")
    assert snapshot["positions"][0]["units"] == Decimal("3")


def test_unknown_lot_schedule_blocks_redemption_without_mutating_book(connection):
    con = connection
    create_account(con, "paper-1", 1000, now=NOW)
    _subscribe(
        con,
        event_id="sub-unknown",
        lot_id="unknown",
        amount=100,
        schedule=None,
        digest=None,
    )

    with pytest.raises(ValueError, match="schedule is unknown"):
        redeem(
            con,
            account_id="paper-1",
            event_id="red-unknown",
            scheme_code="100001",
            isin="INF000000001",
            redemption_date=date(2026, 1, 3),
            redemption_units=1,
            nav_per_unit=100,
            now=NOW,
        )

    assert con.execute(
        "SELECT cash_inr FROM mf_paper_accounts WHERE account_id='paper-1'"
    ).fetchone()[0] == Decimal("900")
    assert (
        con.execute(
            "SELECT units_remaining FROM mf_paper_lots "
            "WHERE account_id='paper-1' AND lot_id='unknown'"
        ).fetchone()[0]
        == 1
    )
    assert (
        con.execute(
            "SELECT count(*) FROM mf_paper_events "
            "WHERE account_id='paper-1' AND kind='REDEEM'"
        ).fetchone()[0]
        == 0
    )


def test_subscription_event_id_cannot_be_reused_for_different_content(connection):
    con = connection
    create_account(con, "paper-1", 1000, now=NOW)
    original = _subscribe(
        con, event_id="sub-same", lot_id="lot-1", amount=100, schedule=(), digest=HASH_A
    )
    assert original["status"] == "applied"
    assert (
        _subscribe(
            con, event_id="sub-same", lot_id="lot-1", amount=100, schedule=(), digest=HASH_A
        )["status"]
        == "already_applied"
    )
    with pytest.raises(MfPaperBookError, match="different event content"):
        _subscribe(
            con, event_id="sub-same", lot_id="lot-1", amount=200, schedule=(), digest=HASH_A
        )
