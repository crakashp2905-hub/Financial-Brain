from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import duckdb
import pytest

from financial_brain.config import Config
from financial_brain.forward_edge.data_permissions import (
    DataPermission,
    DataUse,
    PermissionStatus,
)
from financial_brain.ingest.mf_scheme_terms import persist as persist_scheme_terms
from financial_brain.kernel.fund_transactions import (
    SchemeTransactionCalendar, SchemeTransactionRule, TransactionKind,
)
from financial_brain.kernel.mf_order import (
    MFOrder, MFOrderAcknowledgement, MFOrderEvidenceBasis, MFOrderKind, MFOrderState,
)
from financial_brain.kernel.trading_clock import BusinessCalendar, CalendarDomain
from financial_brain.paper.mf_lot_book import (
    MfPaperBookError,
    account_snapshot,
    create_account,
    redeem,
    subscribe,
)
from financial_brain.paper.mf_redemption_settlement import (
    make_cash_available,
    request_redemption,
    resolve_redemption_nav_eligibility,
    settlement_snapshot,
)
from financial_brain.paper.mf_order_book import record_response, submit_order
from financial_brain.providers.mf_scheme_terms import (
    ExitLoadBand,
    ExitRestriction,
    MFChargeRule,
    SchemeCommercialTerms,
)
from financial_brain.storage.db import Database

HASH_A = "a" * 64
HASH_B = "b" * 64
NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
DECISION = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)
PAPERED = datetime(2026, 1, 16, 12, tzinfo=timezone.utc)


@pytest.fixture
def connection(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    with db.connect() as con:
        yield con


def _permissions(*, include_paper=True):
    uses = [DataUse.RESEARCH, DataUse.RETENTION]
    if include_paper:
        uses.append(DataUse.PAPER_SIMULATION)
    return tuple(
        DataPermission(
            source_id="AMC",
            use=use,
            status=PermissionStatus.USE_ALLOWED,
            effective_from=date(2026, 1, 1),
            effective_through=None,
            evidence_ref="owner-approved-terms",
            reviewer="data-owner",
        )
        for use in uses
    )


def _terms(*, restrictions=(), **changes):
    values = {
        "scheme_code": "100001",
        "plan": "DIRECT",
        "option": "GROWTH",
        "isin": "INF000000001",
        "effective_from": date(2026, 1, 1),
        "effective_to": None,
        "ter_percent": None,
        "aum_inr": None,
        "exit_loads": (),
        "charge_schedule": (),
        "minimum_initial_inr": Decimal("0"),
        "minimum_additional_inr": Decimal("0"),
        "minimum_redemption_inr": Decimal("0"),
        "exit_restrictions": restrictions,
        "source_id": "AMC",
        "source_ref": "reviewed-terms-2026-01",
        "source_sha256": HASH_A,
        "published_at": datetime(2026, 1, 1, 8, tzinfo=timezone.utc),
        "observed_at": datetime(2026, 1, 1, 8, tzinfo=timezone.utc),
    }
    values.update(changes)
    return SchemeCommercialTerms(**values)


def _later_terms(restrictions, *, day, **changes):
    return _terms(
        restrictions=restrictions,
        published_at=datetime(2026, 1, day, 7, tzinfo=timezone.utc),
        observed_at=datetime(2026, 1, day, 8, tzinfo=timezone.utc),
        **changes,
    )


def _subscribe(
    con,
    *,
    event_id,
    lot_id,
    amount,
    schedule,
    digest,
    charge_schedule=(),
    lock_in_until=None,
    account_id="paper-1",
):
    event_day = 2 if digest == HASH_B else 1
    event_date = date(2026, 1, event_day)
    decision_at = datetime(2026, 1, event_day, 12, tzinfo=timezone.utc)
    terms = _terms(
        exit_loads=schedule,
        charge_schedule=charge_schedule,
        source_sha256=digest or HASH_A,
        published_at=datetime(2026, 1, event_day, 8, tzinfo=timezone.utc),
        observed_at=datetime(2026, 1, event_day, 9, tzinfo=timezone.utc),
    )
    permissions = _permissions()
    persist_scheme_terms(con, terms, permissions=permissions)
    result = subscribe(
        con,
        account_id=account_id,
        event_id=event_id,
        lot_id=lot_id,
        scheme_code="100001",
        plan="DIRECT",
        option="GROWTH",
        isin="INF000000001",
        allotted_on=event_date,
        lock_in_until=lock_in_until,
        investment_inr=amount,
        nav_per_unit=100,
        exit_loads=schedule,
        exit_loads_sha256=digest,
        terms=terms,
        permissions=permissions,
        decision_at=decision_at,
        now=decision_at,
    )
    return result, terms


def _redeem(
    con,
    *,
    event_id,
    redemption_date=date(2026, 1, 15),
    units=1,
    terms=None,
    permissions=None,
    decision_at=DECISION,
    nav_per_unit=100,
    account_id="paper-1",
    **kwargs,
):
    selected_terms = terms or _terms()
    selected_permissions = permissions or _permissions()
    persist_scheme_terms(con, selected_terms, permissions=selected_permissions)
    return redeem(
        con,
        account_id=account_id,
        event_id=event_id,
        scheme_code="100001",
        plan="DIRECT",
        option="GROWTH",
        isin="INF000000001",
        redemption_date=redemption_date,
        redemption_units=units,
        nav_per_unit=nav_per_unit,
        terms=selected_terms,
        permissions=selected_permissions,
        decision_at=decision_at,
        now=PAPERED,
        **kwargs,
    )


def _resolve_redemption_nav(con, *, settlement_id, requested_at, units):
    order_id = f"order:{settlement_id}"
    order = MFOrder(
        order_id=order_id, account_id="paper-1", folio_id="synthetic-1",
        scheme_code="100001", plan="DIRECT", option="GROWTH",
        transaction_type=MFOrderKind.REDEMPTION, requested_at=requested_at,
        units=str(units),
    )
    open_days = frozenset(
        day for day in (date(2026, 1, 12) + timedelta(days=offset) for offset in range(25))
        if day.weekday() < 5
    )
    calendar = BusinessCalendar(
        calendar_id="100001", domain=CalendarDomain.SCHEME,
        coverage_start=date(2026, 1, 1), source_as_of=date(2026, 2, 10),
        open_days=open_days, source_ref="scheme-calendar", source_sha256=HASH_B,
        recorded_at=NOW,
    )
    nav_rule = SchemeTransactionRule(
        scheme_id="100001", transaction_kind=TransactionKind.REDEMPTION,
        effective_from=date(2026, 1, 1), effective_to=None, cutoff_local=time(14),
        before_cutoff_lag=0, after_cutoff_lag=1, closed_day_lag=1,
        published_at=NOW, source_ref="redemption-cutoff", source_sha256=HASH_A,
    )
    scheme_calendar = SchemeTransactionCalendar({"100001": calendar}, (nav_rule,))
    submitted = submit_order(
        con, order, valid_business_days=10, calendar=scheme_calendar,
        now=requested_at,
    )
    assert submitted["state"] in {"REQUESTED", "ACCEPTED"}
    accepted_at = datetime(2026, 1, 16, 8, tzinfo=timezone.utc)
    accepted = record_response(
        con, MFOrderAcknowledgement(
            order_id=order_id, state=MFOrderState.ACCEPTED,
            effective_at=accepted_at, received_at=accepted_at + timedelta(seconds=5),
            basis=MFOrderEvidenceBasis.SIMULATED_ASSUMPTION,
        ), now=accepted_at + timedelta(seconds=5),
    )
    assert accepted["state"] == "ACCEPTED"
    return resolve_redemption_nav_eligibility(
        con, account_id="paper-1", settlement_id=settlement_id,
        order_id=order_id, scheme_calendar=scheme_calendar, permissions=(),
        decision_at=datetime(2026, 1, 16, 9, tzinfo=timezone.utc), now=PAPERED,
    )


def test_effective_dated_transaction_charges_reduce_units_and_redemption_proceeds(connection):
    con = connection
    create_account(con, "paper-charges", 1000, now=NOW)
    purchase_rules = (MFChargeRule("STAMP_DUTY", "SUBSCRIBE", "1"),)
    subscribed, _ = _subscribe(
        con, event_id="sub-charged", lot_id="lot-charged", amount=1000,
        schedule=(), digest=HASH_A, account_id="paper-charges",
        charge_schedule=purchase_rules,
    )
    assert subscribed["units"] == Decimal("9.9")
    event = con.execute("""SELECT gross_inr, other_charges_inr, net_inr
        FROM mf_paper_events WHERE account_id='paper-charges' AND event_id='sub-charged'""").fetchone()
    assert tuple(Decimal(value) for value in event) == (
        Decimal("1000"), Decimal("10.00"), Decimal("990.00"),
    )
    redemption_terms = _terms(charge_schedule=(
        MFChargeRule("REDEMPTION_DUTY", "REDEEM", "0.5"),
    ), published_at=datetime(2026, 1, 9, 8, tzinfo=timezone.utc),
       observed_at=datetime(2026, 1, 9, 9, tzinfo=timezone.utc))
    redeemed = _redeem(
        con, event_id="red-charged", units=Decimal("9.9"),
        terms=redemption_terms, account_id="paper-charges",
    )
    assert redeemed["other_charges_inr"] == Decimal("4.95")
    assert redeemed["net_inr"] == Decimal("985.05")
    snapshot = account_snapshot(con, "paper-charges")
    assert snapshot["cash_inr"] == Decimal("985.05")
    ledger = con.execute("""SELECT SUM(debit_inr-credit_inr)
        FROM mf_paper_ledger_entries WHERE account_id='paper-charges'""").fetchone()[0]
    assert Decimal(ledger) == 0


def test_subscription_enforces_sourced_initial_and_additional_minimums(connection):
    con = connection
    create_account(con, "paper-min", 10_000, now=NOW)
    first_terms = _terms(
        minimum_initial_inr=500,
        minimum_additional_inr=250,
    )
    persist_scheme_terms(con, first_terms, permissions=_permissions())
    with pytest.raises(MfPaperBookError, match="below the sourced initial minimum"):
        subscribe(
            con, account_id="paper-min", event_id="min-first-small", lot_id="min-lot-0",
            scheme_code="100001", plan="DIRECT", option="GROWTH", isin="INF000000001",
            allotted_on=date(2026, 1, 1), investment_inr=499, nav_per_unit=100,
            exit_loads=(), exit_loads_sha256=HASH_A, terms=first_terms,
            permissions=_permissions(), decision_at=DECISION, now=PAPERED,
        )
    subscribe(
        con, account_id="paper-min", event_id="min-first", lot_id="min-lot-1",
        scheme_code="100001", plan="DIRECT", option="GROWTH", isin="INF000000001",
        allotted_on=date(2026, 1, 1), investment_inr=500, nav_per_unit=100,
        exit_loads=(), exit_loads_sha256=HASH_A, terms=first_terms,
        permissions=_permissions(), decision_at=DECISION, now=PAPERED,
    )
    with pytest.raises(MfPaperBookError, match="below the sourced additional minimum"):
        subscribe(
            con, account_id="paper-min", event_id="min-add-small", lot_id="min-lot-2",
            scheme_code="100001", plan="DIRECT", option="GROWTH", isin="INF000000001",
            allotted_on=date(2026, 1, 1), investment_inr=249, nav_per_unit=100,
            exit_loads=(), exit_loads_sha256=HASH_A, terms=first_terms,
            permissions=_permissions(), decision_at=DECISION, now=PAPERED,
        )
    subscribe(
        con, account_id="paper-min", event_id="min-add", lot_id="min-lot-3",
        scheme_code="100001", plan="DIRECT", option="GROWTH", isin="INF000000001",
        allotted_on=date(2026, 1, 1), investment_inr=250, nav_per_unit=100,
        exit_loads=(), exit_loads_sha256=HASH_A, terms=first_terms,
        permissions=_permissions(), decision_at=DECISION, now=PAPERED,
    )


def test_subscription_with_unknown_minimum_fails_closed(connection):
    con = connection
    create_account(con, "paper-unknown-min", 10_000, now=NOW)
    terms = _terms(minimum_initial_inr=None)
    persist_scheme_terms(con, terms, permissions=_permissions())
    with pytest.raises(MfPaperBookError, match="subscription minimum is unknown"):
        subscribe(
            con, account_id="paper-unknown-min", event_id="unknown-min", lot_id="unknown-min-lot",
            scheme_code="100001", plan="DIRECT", option="GROWTH", isin="INF000000001",
            allotted_on=date(2026, 1, 1), investment_inr=100, nav_per_unit=100,
            exit_loads=(), exit_loads_sha256=HASH_A, terms=terms,
            permissions=_permissions(), decision_at=DECISION, now=PAPERED,
        )


def test_redemption_respects_sourced_minimum_gross_value(connection):
    con = connection
    create_account(con, "paper-min-red", 1000, now=NOW)
    _, terms = _subscribe(
        con, event_id="sub-min-red", lot_id="lot-min-red", amount=1000,
        schedule=(), digest=HASH_A, account_id="paper-min-red",
    )
    terms = _terms(minimum_redemption_inr=Decimal("500"))
    persist_scheme_terms(con, terms, permissions=_permissions())
    with pytest.raises(MfPaperBookError, match="below the sourced minimum"):
        _redeem(
            con, event_id="red-minimum", units=4, nav_per_unit=100,
            terms=terms, account_id="paper-min-red",
        )
    snapshot = account_snapshot(con, "paper-min-red")
    assert snapshot["positions"][0]["units"] == Decimal("10")
    assert snapshot["cash_inr"] == Decimal("0")


def test_redemption_with_unknown_minimum_fails_closed(connection):
    con = connection
    create_account(con, "paper-unknown-red", 1000, now=NOW)
    _subscribe(
        con, event_id="sub-unknown-red", lot_id="lot-unknown-red", amount=1000,
        schedule=(), digest=HASH_A, account_id="paper-unknown-red",
    )
    with pytest.raises(MfPaperBookError, match="redemption minimum is unknown"):
        _redeem(
            con, event_id="red-unknown-min", units=1,
            terms=_terms(minimum_redemption_inr=None), account_id="paper-unknown-red",
        )
    snapshot = account_snapshot(con, "paper-unknown-red")
    assert snapshot["positions"][0]["units"] == Decimal("10")
    assert snapshot["cash_inr"] == Decimal("0")


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
    _, current_terms = _subscribe(
        con,
        event_id="sub-2",
        lot_id="lot-2",
        amount=500,
        schedule=second_schedule,
        digest=HASH_B,
    )

    result = _redeem(con, event_id="red-1", units=12, terms=current_terms)

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

    replay = _redeem(con, event_id="red-1", units=12, terms=current_terms)
    assert replay["status"] == "already_applied"
    assert con.execute(
        "SELECT cash_inr FROM mf_paper_accounts WHERE account_id='paper-1'"
    ).fetchone()[0] == Decimal("4686")
    snapshot = account_snapshot(con, "paper-1")
    assert snapshot["positions"][0]["units"] == Decimal("3")
    from financial_brain.paper.mf_account_ledger import ledger_snapshot
    journal = ledger_snapshot(con, account_id="paper-1")
    assert journal["is_reconciled"] is True
    assert journal["journal_cash_inr"] == Decimal("4686")
    assert journal["balances_inr"]["MF_INVESTMENTS"] == Decimal("300")
    totals = con.execute("""SELECT SUM(debit_inr), SUM(credit_inr)
        FROM mf_paper_ledger_entries WHERE account_id='paper-1'""").fetchone()
    assert Decimal(totals[0]) == Decimal(totals[1])


def test_unknown_lot_schedule_blocks_redemption_without_mutating_book(connection):
    con = connection
    create_account(con, "paper-1", 1000, now=NOW)
    _, unknown_terms = _subscribe(
        con,
        event_id="sub-unknown",
        lot_id="unknown",
        amount=100,
        schedule=None,
        digest=None,
    )

    with pytest.raises(ValueError, match="schedule is unknown"):
        _redeem(
            con,
            event_id="red-unknown",
            redemption_date=date(2026, 1, 3),
            decision_at=datetime(2026, 1, 3, 12, tzinfo=timezone.utc),
            terms=unknown_terms,
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
    original, _ = _subscribe(
        con, event_id="sub-same", lot_id="lot-1", amount=100, schedule=(), digest=HASH_A
    )
    assert original["status"] == "applied"
    replay, _ = _subscribe(
        con, event_id="sub-same", lot_id="lot-1", amount=100, schedule=(), digest=HASH_A
    )
    assert replay["status"] == "already_applied"
    with pytest.raises(MfPaperBookError, match="different event content"):
        _subscribe(
            con,
            event_id="sub-same",
            lot_id="lot-1",
            amount=200,
            schedule=(),
            digest=HASH_A,
        )


def test_elss_redemption_requires_each_selected_lot_to_be_unlocked(connection):
    con = connection
    create_account(con, "paper-1", 1000, now=NOW)
    _subscribe(
        con,
        event_id="sub-locked",
        lot_id="locked",
        amount=100,
        schedule=(),
        digest=HASH_A,
        lock_in_until=date(2029, 1, 1),
    )
    elss = _later_terms((ExitRestriction.ELSS_LOCK_IN,), day=3)

    with pytest.raises(MfPaperBookError, match="LOT_LOCKED"):
        _redeem(con, event_id="red-locked", terms=elss)
    create_account(con, "paper-2", 1000, now=NOW)
    _subscribe(
        con,
        account_id="paper-2",
        event_id="sub-no-lock-date",
        lot_id="unknown",
        amount=100,
        schedule=(),
        digest=HASH_A,
    )
    with pytest.raises(MfPaperBookError, match="ELSS_LOCK_IN_DATE_UNKNOWN"):
        _redeem(
            con,
            account_id="paper-2",
            event_id="red-unknown-lock",
            terms=elss,
            redemption_date=date(2026, 1, 15),
        )


def test_closed_window_gating_and_segregated_limits_block_impossible_requests(connection):
    con = connection
    create_account(con, "paper-1", 1000, now=NOW)
    _subscribe(
        con,
        event_id="sub-window",
        lot_id="window",
        amount=500,
        schedule=(),
        digest=HASH_A,
    )
    closed_window = _later_terms((ExitRestriction.CLOSED_ENDED_WINDOW,), day=3)
    with pytest.raises(MfPaperBookError, match="REDEMPTION_WINDOW_CLOSED_OR_UNKNOWN"):
        _redeem(con, event_id="red-window-closed", units=1, terms=closed_window)
    assert (
        _redeem(
            con,
            event_id="red-window-open",
            units=1,
            terms=closed_window,
            redemption_window_open=True,
        )["status"]
        == "applied"
    )

    gated = _later_terms((ExitRestriction.GATING,), day=4)
    with pytest.raises(MfPaperBookError, match="GATING_LIMIT"):
        _redeem(
            con,
            event_id="red-gate",
            redemption_date=date(2026, 1, 16),
            decision_at=PAPERED,
            units=2,
            terms=gated,
            gating_remaining_units=1,
        )
    segregated = _later_terms((ExitRestriction.SEGREGATED_PORTFOLIO,), day=5)
    with pytest.raises(MfPaperBookError, match="SEGREGATED_PORTFOLIO_LIMIT"):
        _redeem(
            con,
            event_id="red-segregated",
            redemption_date=date(2026, 1, 16),
            decision_at=PAPERED,
            units=2,
            terms=segregated,
            segregated_portfolio_units=1,
        )


def test_unknown_terms_wrong_identity_and_missing_paper_permission_fail_closed(connection):
    con = connection
    create_account(con, "paper-1", 1000, now=NOW)
    _subscribe(
        con,
        event_id="sub-terms",
        lot_id="terms",
        amount=100,
        schedule=(),
        digest=HASH_A,
    )
    with pytest.raises(MfPaperBookError, match="restrictions are unknown"):
        _redeem(
            con,
            event_id="red-unknown-terms",
            terms=_later_terms(None, day=3),
        )
    with pytest.raises(MfPaperBookError, match="identity does not match"):
        _redeem(
            con,
            event_id="red-wrong-id",
            terms=_terms(isin="INF000000002"),
        )
    with pytest.raises(MfPaperBookError, match="PAPER_SIMULATION permission"):
        _redeem(
            con,
            event_id="red-no-paper-rights",
            permissions=_permissions(include_paper=False),
        )


def test_future_published_terms_are_rejected_at_decision_time(connection):
    con = connection
    create_account(con, "paper-1", 1000, now=NOW)
    _subscribe(
        con,
        event_id="sub-future-terms",
        lot_id="future",
        amount=100,
        schedule=(),
        digest=HASH_A,
    )
    future = _terms(published_at=datetime(2026, 1, 11, tzinfo=timezone.utc))
    with pytest.raises(MfPaperBookError, match="not available at the decision time"):
        _redeem(con, event_id="red-future-terms", terms=future)


def test_migration_adds_identity_and_lock_fields_to_existing_paper_tables(tmp_path):
    cfg = Config(data_root=tmp_path).ensure()
    with duckdb.connect(str(cfg.db_path)) as con:
        con.execute("""CREATE TABLE mf_paper_lots (
            account_id VARCHAR, lot_id VARCHAR, scheme_code VARCHAR, isin VARCHAR,
            allotted_on DATE, units_original DECIMAL(38, 18),
            units_remaining DECIMAL(38, 18), purchase_nav DECIMAL(38, 18),
            exit_loads_json VARCHAR, exit_loads_sha256 VARCHAR,
            subscription_event_id VARCHAR, created_at TIMESTAMPTZ,
            PRIMARY KEY (account_id, lot_id))""")
        con.execute("""CREATE TABLE mf_paper_events (
            account_id VARCHAR, event_id VARCHAR, kind VARCHAR, scheme_code VARCHAR,
            isin VARCHAR, event_date DATE, nav_per_unit DECIMAL(38, 18),
            units DECIMAL(38, 18), gross_inr DECIMAL(38, 18),
            exit_load_inr DECIMAL(38, 18), net_inr DECIMAL(38, 18),
            event_sha256 VARCHAR, created_at TIMESTAMPTZ,
            PRIMARY KEY (account_id, event_id))""")

    Database(cfg).migrate()
    with Database(cfg).connect() as con:
        lot_columns = {
            row[1] for row in con.execute("PRAGMA table_info('mf_paper_lots')").fetchall()
        }
        event_columns = {
            row[1] for row in con.execute("PRAGMA table_info('mf_paper_events')").fetchall()
        }
        settlement_columns = {
            row[1] for row in con.execute(
                "PRAGMA table_info('mf_paper_redemption_settlements')"
            ).fetchall()
        }
        transition_columns = {
            row[1] for row in con.execute(
                "PRAGMA table_info('mf_paper_redemption_transitions')"
            ).fetchall()
        }
    assert {"plan", "option", "lock_in_until"} <= lot_columns
    assert {"plan", "option"} <= event_columns
    assert {"state", "request_sha256", "net_inr", "payout_sha256"} <= settlement_columns
    assert {"sequence", "state", "event_sha256", "evidence_json"} <= transition_columns


def _persist_nav_observation(con, *, observation_id, nav_date, nav, available_at):
    con.execute("""INSERT INTO mf_nav_observations (
        observation_id, scheme_code, isin_growth, isin_reinvest, scheme_name,
        fund_house, scheme_type, plan, option, nav, nav_date, lake_key, row_number,
        row_sha256, source_published_at, observed_at, ingested_at)
        VALUES (?, '100001', 'INF000000001', NULL, 'Example Fund', 'AMC', 'EQUITY',
                'DIRECT', 'GROWTH', ?, ?, 'lake-nav', 1, ?, NULL, ?, ?)""",
        [observation_id, nav, nav_date, HASH_A, available_at, available_at])


def test_redemption_settlement_keeps_unpaid_proceeds_out_of_available_cash(connection):
    con = connection
    create_account(con, "paper-1", 1000, now=NOW)
    _, terms = _subscribe(
        con, event_id="sub-settlement", lot_id="lot-settlement", amount=1000,
        schedule=(), digest=HASH_A,
    )
    request_at = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)
    request = request_redemption(
        con, account_id="paper-1", settlement_id="red-settlement",
        scheme_code="100001", plan="DIRECT", option="GROWTH", isin="INF000000001",
        requested_units=4, requested_on=date(2026, 1, 10), requested_at=request_at,
        now=request_at,
    )
    assert request["state"] == "REDEMPTION_PENDING"
    assert account_snapshot(con, "paper-1")["cash_inr"] == Decimal("0")
    replayed_request = request_redemption(
        con, account_id="paper-1", settlement_id="red-settlement",
        scheme_code="100001", plan="DIRECT", option="GROWTH", isin="INF000000001",
        requested_units=4, requested_on=date(2026, 1, 10), requested_at=request_at,
        now=request_at,
    )
    assert replayed_request["status"] == "already_submitted"
    eligibility = _resolve_redemption_nav(
        con, settlement_id="red-settlement", requested_at=request_at, units=4,
    )
    assert eligibility["eligible_nav_date"] == date(2026, 1, 16)
    assert eligibility["status"] == "resolved"
    assert _resolve_redemption_nav(
        con, settlement_id="red-settlement", requested_at=request_at, units=4,
    )["status"] == "already_resolved"

    with pytest.raises(MfPaperBookError, match="exceed the paper-book precision"):
        request_redemption(
            con, account_id="paper-1", settlement_id="red-precision",
            scheme_code="100001", plan="DIRECT", option="GROWTH", isin="INF000000001",
            requested_units="0.0000000000000000001",
            requested_on=date(2026, 1, 10), requested_at=request_at, now=request_at,
        )

    with pytest.raises(MfPaperBookError, match="unreserved open units"):
        request_redemption(
            con, account_id="paper-1", settlement_id="red-overlap",
            scheme_code="100001", plan="DIRECT", option="GROWTH", isin="INF000000001",
            requested_units=7, requested_on=date(2026, 1, 10), requested_at=request_at,
            now=request_at,
        )

    nav_date = date(2026, 1, 16)
    nav_available_at = datetime(2026, 1, 16, 10, 5, tzinfo=timezone.utc)
    _persist_nav_observation(
        con, observation_id="nav-settlement", nav_date=nav_date, nav=120,
        available_at=nav_available_at,
    )
    decision_at = datetime(2026, 1, 16, 11, tzinfo=timezone.utc)
    with pytest.raises(MfPaperBookError, match="differs from frozen eligibility"):
        _redeem(
            con, event_id="redeem-wrong-nav-date", redemption_date=date(2026, 1, 19), units=4,
            terms=terms, decision_at=decision_at, nav_per_unit=120,
            settlement_id="red-settlement", nav_observation_id="nav-settlement",
            payout_due_date=date(2026, 1, 19),
        )
    result = _redeem(
        con, event_id="redeem-settlement", redemption_date=nav_date, units=4,
        terms=terms, decision_at=decision_at, nav_per_unit=120,
        settlement_id="red-settlement", nav_observation_id="nav-settlement",
        payout_due_date=date(2026, 1, 19),
    )
    assert result["state"] == "PAYOUT_PENDING"
    assert result["net_inr"] == Decimal("480.00")
    snapshot = account_snapshot(con, "paper-1")
    assert snapshot["cash_inr"] == Decimal("0")
    assert snapshot["pending_redemption_inr"] == Decimal("480.00")
    assert snapshot["cash_plus_pending_redemption_inr"] == Decimal("480.00")
    from financial_brain.paper.mf_account_ledger import ledger_snapshot
    pending_ledger = ledger_snapshot(con, account_id="paper-1")
    assert pending_ledger["is_reconciled"] is True
    assert pending_ledger["journal_balanced"] is True
    assert pending_ledger["balances_inr"]["CASH"] == 0
    assert pending_ledger["balances_inr"]["REDEMPTION_RECEIVABLE"] == Decimal("480.00")
    assert pending_ledger["balances_inr"]["MF_INVESTMENTS"] == Decimal("600")
    assert pending_ledger["balances_inr"]["REALIZED_GAIN"] == Decimal("-80.00")

    settlement = settlement_snapshot(
        con, account_id="paper-1", settlement_id="red-settlement",
    )
    assert settlement["state"] == "PAYOUT_PENDING"
    assert settlement["nav_observation_id"] == "nav-settlement"
    assert settlement["eligible_nav_date"] == nav_date
    assert settlement["order_id"] == "order:red-settlement"
    assert [item[0] for item in settlement["transitions"]] == [
        "REDEMPTION_PENDING", "VALUE_CONFIRMED", "PAYOUT_PENDING",
    ]

    early = datetime(2026, 1, 18, 12, tzinfo=timezone.utc)
    with pytest.raises(MfPaperBookError, match="cannot precede its due date"):
        make_cash_available(
            con, account_id="paper-1", settlement_id="red-settlement",
            available_at=early, now=early,
        )
    assert account_snapshot(con, "paper-1")["cash_inr"] == Decimal("0")
    observed_at = datetime(2026, 1, 19, 12, tzinfo=timezone.utc)
    with pytest.raises(MfPaperBookError, match="PAPER_SIMULATION permission"):
        make_cash_available(
            con, account_id="paper-1", settlement_id="red-settlement",
            available_at=observed_at, basis="OBSERVED_EVIDENCE",
            evidence_ref="payout-receipt", source_id="AMC", source_sha256=HASH_A,
            permissions=(), now=observed_at,
        )
    assert account_snapshot(con, "paper-1")["cash_inr"] == Decimal("0")

    paid_at = datetime(2026, 1, 19, 12, tzinfo=timezone.utc)
    payout = make_cash_available(
        con, account_id="paper-1", settlement_id="red-settlement",
        available_at=paid_at, now=paid_at,
    )
    assert payout["state"] == "CASH_AVAILABLE"
    assert payout["basis"] == "SIMULATED_ASSUMPTION"
    assert account_snapshot(con, "paper-1")["cash_inr"] == Decimal("480.00")
    assert account_snapshot(con, "paper-1")["pending_redemption_inr"] == Decimal("0")
    paid_ledger = ledger_snapshot(con, account_id="paper-1")
    assert paid_ledger["is_reconciled"] is True
    assert paid_ledger["journal_balanced"] is True
    assert paid_ledger["balances_inr"]["CASH"] == Decimal("480.00")
    assert paid_ledger["balances_inr"]["REDEMPTION_RECEIVABLE"] == 0

    replay = make_cash_available(
        con, account_id="paper-1", settlement_id="red-settlement",
        available_at=paid_at, now=paid_at,
    )
    assert replay["status"] == "already_applied"
    assert account_snapshot(con, "paper-1")["cash_inr"] == Decimal("480.00")
    assert settlement_snapshot(
        con, account_id="paper-1", settlement_id="red-settlement",
    )["state"] == "CASH_AVAILABLE"


def test_account_valuation_uses_only_as_known_nav_and_flags_stale_or_missing(connection):
    con = connection
    create_account(con, "paper-valuation", 1000, now=NOW)
    _subscribe(
        con, event_id="valuation-sub", lot_id="valuation-lot", amount=1000,
        schedule=(), digest=HASH_A, account_id="paper-valuation",
    )
    nav_date = date(2026, 1, 15)
    available = datetime(2026, 1, 15, 12, tzinfo=timezone.utc)
    _persist_nav_observation(
        con, observation_id="valuation-nav", nav_date=nav_date,
        nav=Decimal("125.5"), available_at=available,
    )
    before_ingestion = account_snapshot(
        con, "paper-valuation", known_at=datetime(2026, 1, 15, 11, tzinfo=timezone.utc),
    )
    assert before_ingestion["positions"][0]["valuation_status"] == "NAV_UNAVAILABLE"
    assert before_ingestion["positions"][0]["market_value_inr"] is None

    known = account_snapshot(
        con, "paper-valuation", known_at=datetime(2026, 1, 15, 13, tzinfo=timezone.utc),
    )
    assert known["positions"][0]["valuation_status"] == "CURRENT"
    assert known["positions"][0]["market_value_inr"] == Decimal("1255.0")
    assert known["positions"][0]["unrealized_pnl_inr"] == Decimal("255.0")

    stale = account_snapshot(
        con, "paper-valuation", known_at=datetime(2026, 2, 1, 13, tzinfo=timezone.utc),
    )
    assert stale["positions"][0]["valuation_status"] == "STALE"
    assert stale["stale_position_count"] == 1


def test_mf_terminal_panel_displays_last_known_nav_with_stale_flag(connection):
    from financial_brain.paper.terminal import mf_paper_panel

    con = connection
    create_account(con, "paper-terminal", 1000, now=NOW)
    _subscribe(
        con, event_id="terminal-sub", lot_id="terminal-lot", amount=1000,
        schedule=(), digest=HASH_A, account_id="paper-terminal",
    )
    _persist_nav_observation(
        con, observation_id="terminal-nav", nav_date=date(2026, 1, 15), nav=125,
        available_at=datetime(2026, 1, 15, 12, tzinfo=timezone.utc),
    )
    page = mf_paper_panel(con, date(2026, 2, 1))
    assert "NAV value Rs 1,250.00" in page
    assert "Rs +250.00" in page
    assert "Last known NAV" in page and "125.0000" in page
    assert "STALE" in page and "NAV date" in page


def test_mf_terminal_panel_distinguishes_simulated_and_observed_payouts(connection):
    from financial_brain.paper.mf_redemption_settlement import (
        make_cash_available, request_redemption,
    )
    from financial_brain.paper.terminal import mf_paper_panel

    con = connection
    create_account(con, "paper-payout-basis", 1000, now=NOW)
    _subscribe(
        con, event_id="payout-basis-sub", lot_id="payout-basis-lot", amount=1000,
        schedule=(), digest=HASH_A, account_id="paper-payout-basis",
    )
    requested_at = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)
    request_redemption(
        con, account_id="paper-payout-basis", settlement_id="simulated-payout",
        scheme_code="100001", plan="DIRECT", option="GROWTH", isin="INF000000001",
        requested_units=1, requested_on=date(2026, 1, 10),
        requested_at=requested_at, now=requested_at,
    )
    # Use a settlement state available to make_cash_available and assert the
    # terminal does not present assumptions as independently observed evidence.
    con.execute("""UPDATE mf_paper_redemption_settlements SET state='PAYOUT_PENDING',
        net_inr=100, payout_due_date=? WHERE account_id=? AND settlement_id=?""",
        [date(2026, 1, 12), "paper-payout-basis", "simulated-payout"])
    payout_at = datetime(2026, 1, 12, 12, tzinfo=timezone.utc)
    make_cash_available(
        con, account_id="paper-payout-basis", settlement_id="simulated-payout",
        available_at=payout_at, now=payout_at,
    )

    request_redemption(
        con, account_id="paper-payout-basis", settlement_id="observed-payout",
        scheme_code="100001", plan="DIRECT", option="GROWTH", isin="INF000000001",
        requested_units=1, requested_on=date(2026, 1, 10),
        requested_at=requested_at, now=requested_at,
    )
    con.execute("""UPDATE mf_paper_redemption_settlements SET state='PAYOUT_PENDING',
        net_inr=100, payout_due_date=? WHERE account_id=? AND settlement_id=?""",
        [date(2026, 1, 12), "paper-payout-basis", "observed-payout"])
    permission = DataPermission(
        source_id="BANK", use=DataUse.PAPER_SIMULATION,
        status=PermissionStatus.USE_ALLOWED, effective_from=date(2026, 1, 1),
        effective_through=None, evidence_ref="reviewed-bank-terms", reviewer="owner",
    )
    make_cash_available(
        con, account_id="paper-payout-basis", settlement_id="observed-payout",
        available_at=payout_at, now=payout_at, basis="OBSERVED_EVIDENCE",
        evidence_ref="statement-row-42", source_id="BANK", source_sha256=HASH_B,
        permissions=(permission,),
    )

    page = mf_paper_panel(con, date(2026, 1, 12))
    assert "SIMULATED_ASSUMPTION" in page
    assert "OBSERVED_EVIDENCE" in page
    assert "statement-row-42" in page and "BANK" in page and HASH_B[:12] in page
    assert page.index("observed-payout") < page.index("simulated-payout")


def test_settlement_value_requires_exact_as_known_nav_observation(connection):
    con = connection
    create_account(con, "paper-1", 1000, now=NOW)
    _, terms = _subscribe(
        con, event_id="sub-nav-gate", lot_id="lot-nav-gate", amount=1000,
        schedule=(), digest=HASH_A,
    )
    requested_at = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)
    request_redemption(
        con, account_id="paper-1", settlement_id="red-nav-gate",
        scheme_code="100001", plan="DIRECT", option="GROWTH", isin="INF000000001",
        requested_units=1, requested_on=date(2026, 1, 10), requested_at=requested_at,
        now=requested_at,
    )
    _resolve_redemption_nav(
        con, settlement_id="red-nav-gate", requested_at=requested_at, units=1,
    )
    nav_date = date(2026, 1, 16)
    nav_at = datetime(2026, 1, 16, 10, tzinfo=timezone.utc)
    _persist_nav_observation(
        con, observation_id="nav-exact", nav_date=nav_date, nav=100,
        available_at=nav_at,
    )
    with pytest.raises(MfPaperBookError, match="as-known observation"):
        _redeem(
            con, event_id="redeem-nav-gate", redemption_date=nav_date, units=1,
            terms=terms, decision_at=datetime(2026, 1, 16, 11, tzinfo=timezone.utc),
            settlement_id="red-nav-gate", nav_observation_id="wrong-observation",
            payout_due_date=date(2026, 1, 19),
        )
    assert settlement_snapshot(
        con, account_id="paper-1", settlement_id="red-nav-gate",
    )["state"] == "REDEMPTION_PENDING"
