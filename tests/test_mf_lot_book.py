from datetime import date, datetime, timezone
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
    settlement_snapshot,
)
from financial_brain.providers.mf_scheme_terms import (
    ExitLoadBand,
    ExitRestriction,
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
        "minimum_initial_inr": None,
        "minimum_additional_inr": None,
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
    lock_in_until=None,
    account_id="paper-1",
):
    event_day = 2 if digest == HASH_B else 1
    event_date = date(2026, 1, event_day)
    decision_at = datetime(2026, 1, event_day, 12, tzinfo=timezone.utc)
    terms = _terms(
        exit_loads=schedule,
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

    settlement = settlement_snapshot(
        con, account_id="paper-1", settlement_id="red-settlement",
    )
    assert settlement["state"] == "PAYOUT_PENDING"
    assert settlement["nav_observation_id"] == "nav-settlement"
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

    replay = make_cash_available(
        con, account_id="paper-1", settlement_id="red-settlement",
        available_at=paid_at, now=paid_at,
    )
    assert replay["status"] == "already_applied"
    assert account_snapshot(con, "paper-1")["cash_inr"] == Decimal("480.00")
    assert settlement_snapshot(
        con, account_id="paper-1", settlement_id="red-settlement",
    )["state"] == "CASH_AVAILABLE"


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
