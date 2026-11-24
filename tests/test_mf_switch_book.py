from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.config import Config
from financial_brain.forward_edge.data_permissions import (
    DataPermission, DataUse, PermissionStatus,
)
from financial_brain.ingest.mf_scheme_terms import persist as persist_terms
from financial_brain.kernel.fund_transactions import (
    SchemeTransactionCalendar, SchemeTransactionRule, TransactionKind,
)
from financial_brain.kernel.mf_order import (
    MFOrder, MFOrderAcknowledgement, MFOrderEvidenceBasis,
    MFOrderKind, MFOrderState,
)
from financial_brain.kernel.mf_switch import MFSwitchRequest
from financial_brain.kernel.trading_clock import BusinessCalendar, CalendarDomain
from financial_brain.paper.mf_account_ledger import (
    ledger_snapshot, mf_instrument_id, post_journal,
)
from financial_brain.paper.mf_lot_book import create_account, redeem
from financial_brain.paper.mf_order_book import record_response, submit_order
from financial_brain.paper.mf_purchase_book import resolve_eligible_nav
from financial_brain.paper.mf_redemption_settlement import make_cash_available
from financial_brain.paper.mf_switch_book import (
    MFSwitchBookError, apply_switch_in_allotment, apply_switch_in_funds,
    confirm_switch_out_proceeds, confirm_switch_out_value, link_switch_in_order,
    link_switch_out_order, request_switch, resolve_switch_out_nav, switch_snapshot,
)
from financial_brain.providers.mf_scheme_terms import SchemeCommercialTerms
from financial_brain.storage.db import Database


UTC = timezone.utc
HASH = "a" * 64
ACCOUNT = "paper-switch"
FOLIO = "synthetic-folio"
SOURCE = ("111111", "DIRECT", "GROWTH", "INF111111111")
TARGET = ("222222", "DIRECT", "GROWTH", "INF222222222")
START = datetime(2026, 11, 23, 8, tzinfo=UTC)


@pytest.fixture
def con(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    with db.connect() as connection:
        yield connection


def _permissions():
    permissions = tuple(DataPermission(
        source_id="AMC", use=use, status=PermissionStatus.USE_ALLOWED,
        effective_from=date(2026, 1, 1), effective_through=None,
        evidence_ref="switch-terms", reviewer="owner",
    ) for use in (DataUse.RESEARCH, DataUse.RETENTION, DataUse.PAPER_SIMULATION))
    return permissions + (DataPermission(
        source_id="AMFI", use=DataUse.PAPER_SIMULATION,
        status=PermissionStatus.USE_ALLOWED,
        effective_from=date(2026, 1, 1), effective_through=None,
        evidence_ref="amfi-paper-nav", reviewer="owner",
    ),)


def _terms(identity):
    scheme, plan, option, isin = identity
    return SchemeCommercialTerms(
        scheme_code=scheme, plan=plan, option=option, isin=isin,
        effective_from=date(2026, 1, 1), effective_to=None, ter_percent=None,
        aum_inr=None, exit_loads=(), charge_schedule=(),
        minimum_initial_inr=Decimal(0), minimum_additional_inr=Decimal(0),
        minimum_redemption_inr=Decimal(0), exit_restrictions=(), source_id="AMC",
        source_ref=f"terms-{scheme}", source_sha256=HASH,
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _calendar():
    days = frozenset(date(2026, 11, day) for day in range(20, 29)
                     if date(2026, 11, day).weekday() < 5)
    calendars = {
        scheme: BusinessCalendar(
            calendar_id=scheme, domain=CalendarDomain.SCHEME,
            coverage_start=date(2026, 11, 20), source_as_of=date(2026, 11, 28),
            open_days=days, source_ref=f"calendar-{scheme}", source_sha256=HASH,
            recorded_at=datetime(2026, 11, 20, tzinfo=UTC),
        ) for scheme, *_ in (SOURCE, TARGET)
    }
    rules = (
        SchemeTransactionRule(
            scheme_id=SOURCE[0], transaction_kind=TransactionKind.REDEMPTION,
            effective_from=date(2026, 1, 1), effective_to=None, cutoff_local=time(23, 59),
            before_cutoff_lag=0, after_cutoff_lag=1, closed_day_lag=1,
            published_at=datetime(2026, 1, 1, tzinfo=UTC), source_ref="redemption-rule",
            source_sha256=HASH,
        ),
        SchemeTransactionRule(
            scheme_id=TARGET[0], transaction_kind=TransactionKind.SWITCH_IN,
            effective_from=date(2026, 1, 1), effective_to=None, cutoff_local=time(23, 59),
            before_cutoff_lag=0, after_cutoff_lag=1, closed_day_lag=1,
            published_at=datetime(2026, 1, 1, tzinfo=UTC), source_ref="switch-in-rule",
            source_sha256=HASH,
        ),
    )
    return SchemeTransactionCalendar(calendars, rules)


def _order(con, *, order_id, identity, kind, requested_at, units=None, amount=None, calendar):
    order = MFOrder(
        order_id=order_id, account_id=ACCOUNT, folio_id=FOLIO,
        scheme_code=identity[0], plan=identity[1], option=identity[2],
        transaction_type=kind, requested_at=requested_at,
        units=units, amount_inr=amount,
    )
    submit_order(con, order, valid_business_days=3, calendar=calendar, now=requested_at)
    accepted_at = requested_at + timedelta(minutes=1)
    record_response(con, MFOrderAcknowledgement(
        order_id=order_id, state=MFOrderState.ACCEPTED,
        effective_at=accepted_at, received_at=accepted_at,
        basis=MFOrderEvidenceBasis.SIMULATED_ASSUMPTION,
    ), now=accepted_at)
    return order


def _nav(con, *, observation_id, identity, nav_date, available_at, nav="100"):
    con.execute("""INSERT INTO mf_nav_observations (
        observation_id, scheme_code, isin_growth, isin_reinvest, scheme_name,
        fund_house, scheme_type, plan, option, nav, nav_date, lake_key,
        row_number, row_sha256, source_published_at, observed_at, ingested_at)
        VALUES (?, ?, ?, NULL, 'Switch Test Scheme', 'AMC', 'EQUITY', ?, ?, ?, ?,
        'switch-test-lake', 1, ?, NULL, ?, ?)""",
        [observation_id, identity[0], identity[3], identity[1], identity[2], nav,
         nav_date, HASH, available_at, available_at])


def test_switch_persists_both_legs_and_never_reuses_unsettled_cash(con):
    calendar = _calendar()
    permissions = _permissions()
    create_account(con, ACCOUNT, 0, now=START)
    for identity in (SOURCE, TARGET):
        persist_terms(con, _terms(identity), permissions=permissions)
    con.execute("""INSERT INTO mf_paper_lots (
        account_id, lot_id, folio_id, scheme_code, plan, option, isin, allotted_on, lock_in_until,
        units_original, units_remaining, purchase_nav, exit_loads_json, exit_loads_sha256,
        subscription_event_id, created_at)
        VALUES (?, 'source-lot', ?, ?, ?, ?, ?, ?, NULL, 10, 10, 100, '[]', ?, 'source-sub', ?)""",
        [ACCOUNT, FOLIO, *SOURCE, date(2026, 11, 20), HASH, START])
    post_journal(
        con, account_id=ACCOUNT, transaction_id="opening-switch-source-units",
        effective_at=START, recorded_at=START,
        unit_deltas=((FOLIO, mf_instrument_id(*SOURCE), Decimal(10)),),
        evidence={"fixture": "opening source units for switch paper test"},
    )

    request = MFSwitchRequest(
        switch_id="switch-1", account_id=ACCOUNT, folio_id=FOLIO,
        source_scheme=SOURCE[0], source_plan=SOURCE[1], source_option=SOURCE[2],
        source_isin=SOURCE[3], target_scheme=TARGET[0], target_plan=TARGET[1],
        target_option=TARGET[2], target_isin=TARGET[3], requested_units=Decimal(10),
        requested_at=START,
    )
    request_switch(con, request=request, settlement_id="switch-out-1", now=START)
    assert request_switch(
        con, request=request, settlement_id="switch-out-1", now=START,
    )["status"] == "already_requested"
    out_order = _order(
        con, order_id="switch-out-order", identity=SOURCE,
        kind=MFOrderKind.SWITCH_OUT, requested_at=START, units=Decimal(10),
        calendar=calendar,
    )
    link_switch_out_order(
        con, account_id=ACCOUNT, switch_id="switch-1", order_id=out_order.order_id,
        now=START + timedelta(minutes=2),
    )
    resolve_switch_out_nav(
        con, account_id=ACCOUNT, switch_id="switch-1", scheme_calendar=calendar,
        decision_at=START + timedelta(minutes=2), now=START + timedelta(minutes=3),
    )
    _nav(con, observation_id="nav-source", identity=SOURCE,
         nav_date=date(2026, 11, 23), available_at=datetime(2026, 11, 23, 12, tzinfo=UTC))
    source_terms = _terms(SOURCE)
    redeemed = redeem(
        con, account_id=ACCOUNT, folio_id=FOLIO,
        event_id="switch-out-value", scheme_code=SOURCE[0],
        plan=SOURCE[1], option=SOURCE[2], isin=SOURCE[3],
        redemption_date=date(2026, 11, 23), redemption_units=Decimal(10),
        nav_per_unit=Decimal(100), terms=source_terms, permissions=permissions,
        decision_at=datetime(2026, 11, 23, 12, 2, tzinfo=UTC), now=datetime(2026, 11, 23, 12, 3, tzinfo=UTC),
        settlement_id="switch-out-1", nav_observation_id="nav-source",
        payout_due_date=date(2026, 11, 24),
    )
    assert redeemed["net_inr"] == Decimal(1000)
    confirm_switch_out_value(
        con, account_id=ACCOUNT, switch_id="switch-1",
        now=datetime(2026, 11, 23, 12, 4, tzinfo=UTC),
    )
    with pytest.raises(MFSwitchBookError, match="not in available account cash"):
        confirm_switch_out_proceeds(
            con, account_id=ACCOUNT, switch_id="switch-1",
            now=datetime(2026, 11, 23, 12, 5, tzinfo=UTC),
        )
    payout_at = datetime(2026, 11, 24, 6, tzinfo=UTC)
    make_cash_available(
        con, account_id=ACCOUNT, settlement_id="switch-out-1",
        available_at=payout_at, now=payout_at + timedelta(minutes=1),
    )
    confirm_switch_out_proceeds(
        con, account_id=ACCOUNT, switch_id="switch-1", now=payout_at + timedelta(minutes=2),
    )

    same_time_order = _order(
        con, order_id="switch-in-same-time", identity=TARGET,
        kind=MFOrderKind.SWITCH_IN, requested_at=payout_at,
        amount=Decimal(900), calendar=calendar,
    )
    with pytest.raises(MFSwitchBookError, match="requested after source proceeds"):
        link_switch_in_order(
            con, account_id=ACCOUNT, switch_id="switch-1",
            order_id=same_time_order.order_id, now=payout_at + timedelta(minutes=3),
        )

    in_requested = payout_at + timedelta(minutes=10)
    in_order = _order(
        con, order_id="switch-in-order", identity=TARGET,
        kind=MFOrderKind.SWITCH_IN, requested_at=in_requested,
        amount=Decimal(900), calendar=calendar,
    )
    link_switch_in_order(
        con, account_id=ACCOUNT, switch_id="switch-1", order_id=in_order.order_id,
        now=in_requested + timedelta(minutes=2),
    )
    apply_switch_in_funds(
        con, account_id=ACCOUNT, switch_id="switch-1",
        initiated_at=in_requested + timedelta(minutes=3),
        credited_at=in_requested + timedelta(minutes=5),
        available_at=in_requested + timedelta(minutes=8),
        recorded_at=in_requested + timedelta(minutes=10),
    )
    # Payment application reserves cash before the later target NAV is known.
    cash = con.execute("SELECT cash_inr FROM mf_paper_accounts WHERE account_id=?", [ACCOUNT]).fetchone()[0]
    assert Decimal(cash) == Decimal(100)
    assert apply_switch_in_funds(
        con, account_id=ACCOUNT, switch_id="switch-1",
        initiated_at=in_requested + timedelta(minutes=3),
        credited_at=in_requested + timedelta(minutes=5),
        available_at=in_requested + timedelta(minutes=8),
        recorded_at=in_requested + timedelta(minutes=10),
    )["status"] == "already_applied"

    resolve_eligible_nav(
        con, order_id=in_order.order_id, scheme_calendar=calendar,
        permissions=permissions, decision_at=datetime(2026, 11, 24, 8, tzinfo=UTC),
        now=datetime(2026, 11, 24, 8, 1, tzinfo=UTC),
    )
    _nav(con, observation_id="nav-target", identity=TARGET,
         nav_date=date(2026, 11, 24), available_at=datetime(2026, 11, 24, 10, tzinfo=UTC))
    allotted_at = datetime(2026, 11, 24, 12, 2, tzinfo=UTC)
    applied = apply_switch_in_allotment(
        con, account_id=ACCOUNT, switch_id="switch-1", nav_observation_id="nav-target",
        nav_per_unit=Decimal(100), terms=_terms(TARGET), permissions=permissions,
        decision_at=datetime(2026, 11, 24, 12, 1, tzinfo=UTC),
        allotted_at=allotted_at, now=allotted_at + timedelta(minutes=1),
    )
    assert applied["units"] == Decimal(9)
    result = switch_snapshot(con, account_id=ACCOUNT, switch_id="switch-1")
    assert result["state"] == "SWITCH_IN_ALLOTMENT_CONFIRMED"
    assert [row[1] for row in result["transitions"]] == [
        "REQUESTED", "SWITCH_OUT_VALUE_CONFIRMED", "SWITCH_OUT_PROCEEDS_AVAILABLE",
        "SWITCH_IN_FUNDS_APPLIED", "SWITCH_IN_ALLOTMENT_CONFIRMED",
    ]
    positions = con.execute("""SELECT scheme_code, units_remaining FROM mf_paper_lots
        WHERE account_id=? AND units_remaining>0 ORDER BY scheme_code""", [ACCOUNT]).fetchall()
    assert positions == [(TARGET[0], Decimal(9))]
    ledger = ledger_snapshot(con, account_id=ACCOUNT)
    assert ledger["cash_inr"] == Decimal(100)
    assert ledger["is_reconciled"] is True
    ledger_total = con.execute("""SELECT SUM(debit_inr-credit_inr)
        FROM mf_paper_ledger_entries WHERE account_id=?""", [ACCOUNT]).fetchone()[0]
    assert Decimal(ledger_total) == 0
