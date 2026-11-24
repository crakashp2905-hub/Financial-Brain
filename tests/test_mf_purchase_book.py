from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.config import Config
from financial_brain.forward_edge.data_permissions import (
    DataPermission, DataUse, PermissionStatus,
)
from financial_brain.ingest.mf_scheme_terms import persist as persist_scheme_terms
from financial_brain.kernel.fund_transactions import (
    SchemeTransactionCalendar, SchemeTransactionRule, TransactionKind,
)
from financial_brain.kernel.mf_order import (
    MFOrder, MFOrderAcknowledgement, MFOrderEvidenceBasis, MFOrderKind, MFOrderState,
)
from financial_brain.kernel.units import NavPerUnit
from financial_brain.kernel.mf_purchase_payment import (
    MFPurchasePaymentMilestone, PaymentEvidenceBasis, PaymentMilestoneKind,
)
from financial_brain.kernel.trading_clock import BusinessCalendar, CalendarDomain
from financial_brain.paper.mf_account_ledger import ledger_snapshot
from financial_brain.paper.mf_lot_book import account_snapshot, create_account
from financial_brain.paper.mf_order_book import record_response, submit_order
from financial_brain.paper.mf_purchase_book import (
    MFPaperPurchaseError, apply_allotment, record_payment_milestone, resolve_eligible_nav,
)
from financial_brain.providers.mf_scheme_terms import SchemeCommercialTerms
from financial_brain.storage.db import Database


UTC = timezone.utc
REQUESTED = datetime(2026, 11, 23, 4, tzinfo=UTC)


@pytest.fixture
def setup_purchase(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    with db.connect() as con:
        create_account(con, "paper-1", 10000, now=REQUESTED)
        order = MFOrder(
            order_id="purchase-1", account_id="paper-1", folio_id="SYNTHETIC-1",
            scheme_code="123456", plan="DIRECT", option="GROWTH",
            transaction_type=MFOrderKind.PURCHASE, requested_at=REQUESTED,
            amount_inr=Decimal("1000"),
        )
        business = BusinessCalendar(
            calendar_id="123456", domain=CalendarDomain.SCHEME,
            coverage_start=date(2026, 11, 23), source_as_of=date(2026, 11, 25),
            open_days=frozenset(date(2026, 11, day) for day in (23, 24, 25)),
            source_ref="scheme-calendar", source_sha256="d" * 64,
            recorded_at=REQUESTED - timedelta(days=1),
        )
        rule = SchemeTransactionRule(
            scheme_id="123456", transaction_kind=TransactionKind.PURCHASE,
            effective_from=date(2026, 11, 1), effective_to=None,
            cutoff_local=time(15), before_cutoff_lag=0, after_cutoff_lag=1,
            closed_day_lag=1, published_at=REQUESTED - timedelta(days=1),
            source_ref="scheme-cutoff", source_sha256="e" * 64,
        )
        calendar = SchemeTransactionCalendar({"123456": business}, (rule,))
        submit_order(con, order, valid_business_days=2, calendar=calendar,
                     now=REQUESTED + timedelta(seconds=2))
        accepted_at = REQUESTED + timedelta(minutes=1)
        accepted = MFOrderAcknowledgement(
            order_id=order.order_id, state=MFOrderState.ACCEPTED,
            effective_at=accepted_at, received_at=accepted_at + timedelta(seconds=5),
            basis=MFOrderEvidenceBasis.SIMULATED_ASSUMPTION,
        )
        record_response(con, accepted, now=accepted.received_at)
        milestones = (
            MFPurchasePaymentMilestone(
                PaymentMilestoneKind.INITIATED, REQUESTED + timedelta(seconds=10),
                REQUESTED + timedelta(seconds=11), PaymentEvidenceBasis.SIMULATED_ASSUMPTION,
            ),
            MFPurchasePaymentMilestone(
                PaymentMilestoneKind.CREDITED_TO_SCHEME,
                datetime(2026, 11, 23, 10, 5, tzinfo=UTC),
                datetime(2026, 11, 23, 10, 5, 1, tzinfo=UTC),
                PaymentEvidenceBasis.SIMULATED_ASSUMPTION,
            ),
            MFPurchasePaymentMilestone(
                PaymentMilestoneKind.AVAILABLE_FOR_UTILIZATION,
                datetime(2026, 11, 23, 10, 15, tzinfo=UTC),
                datetime(2026, 11, 23, 10, 15, 1, tzinfo=UTC),
                PaymentEvidenceBasis.SIMULATED_ASSUMPTION,
            ),
        )
        for milestone in milestones:
            record_payment_milestone(
                con, order_id=order.order_id, milestone=milestone,
                now=milestone.received_at,
            )
        permissions = tuple(DataPermission(
            source_id=source, use=use, status=PermissionStatus.USE_ALLOWED,
            effective_from=date(2026, 11, 1), effective_through=None,
            evidence_ref=f"{source}-paper-use", reviewer="owner",
        ) for source, use in (
            ("AMFI", DataUse.PAPER_SIMULATION),
            ("AMC", DataUse.PAPER_SIMULATION),
            ("AMC", DataUse.RESEARCH),
            ("AMC", DataUse.RETENTION),
        ))
        terms = SchemeCommercialTerms(
            scheme_code="123456", plan="DIRECT", option="GROWTH", isin="INF123456789",
            effective_from=date(2026, 11, 1), effective_to=None, ter_percent=None,
            aum_inr=None, exit_loads=(), minimum_initial_inr=Decimal(0),
            charge_schedule=(),
            minimum_additional_inr=Decimal(0), minimum_redemption_inr=Decimal(0),
            exit_restrictions=(), source_id="AMC", source_ref="terms", source_sha256="f" * 64,
            published_at=REQUESTED - timedelta(days=1), observed_at=REQUESTED - timedelta(days=1),
        )
        persist_scheme_terms(con, terms, permissions=permissions)
        yield con, order, calendar, permissions, terms


def _insert_nav(con, *, nav_date, observation_id, available_at, nav=10):
    con.execute("""INSERT INTO mf_nav_observations (
        observation_id, scheme_code, isin_growth, isin_reinvest, scheme_name,
        fund_house, scheme_type, plan, option, nav, nav_date, lake_key,
        row_number, row_sha256, source_published_at, observed_at, ingested_at)
        VALUES (?, '123456', 'INF123456789', NULL, 'Test Scheme', 'AMC',
        'EQUITY', 'DIRECT', 'GROWTH', ?, ?, 'purchase-nav-lake',
        1, ?, NULL, ?, ?)""",
        [observation_id, nav, nav_date, "a" * 64, available_at, available_at])


def test_purchase_freezes_eligible_date_stays_nav_pending_then_allots_exact_nav(setup_purchase):
    con, order, calendar, permissions, terms = setup_purchase
    decision = datetime(2026, 11, 23, 12, tzinfo=UTC)
    eligible = resolve_eligible_nav(
        con, order_id=order.order_id, scheme_calendar=calendar, permissions=permissions,
        decision_at=decision, now=decision + timedelta(seconds=1),
    )
    assert eligible["state"] == "NAV_PENDING"
    assert eligible["eligible_nav_date"] == date(2026, 11, 24)
    assert resolve_eligible_nav(
        con, order_id=order.order_id, scheme_calendar=calendar, permissions=permissions,
        decision_at=decision, now=decision + timedelta(seconds=1),
    )["status"] == "already_resolved"

    _insert_nav(con, nav_date=date(2026, 11, 23), observation_id="wrong-date",
                available_at=datetime(2026, 11, 23, 11, tzinfo=UTC))
    with pytest.raises(MFPaperPurchaseError, match="NAV_PENDING"):
        apply_allotment(
            con, order_id=order.order_id, nav_observation_id="wrong-date", nav_per_unit=10,
            terms=terms, permissions=permissions, decision_at=decision,
            allotted_at=decision + timedelta(minutes=1), now=decision + timedelta(minutes=2),
        )
    assert account_snapshot(con, account_id="paper-1")["cash_inr"] == Decimal("10000")

    nav_available = datetime(2026, 11, 24, 4, 30, tzinfo=UTC)
    _insert_nav(con, nav_date=date(2026, 11, 24), observation_id="eligible-nav",
                available_at=nav_available, nav=7)
    decision = nav_available + timedelta(minutes=1)
    result = apply_allotment(
            con, order_id=order.order_id, nav_observation_id="eligible-nav", nav_per_unit=NavPerUnit(7),
        terms=terms, permissions=permissions, decision_at=decision,
        allotted_at=decision + timedelta(minutes=1), now=decision + timedelta(minutes=2),
    )
    assert result["state"] == "ALLOTTED"
    expected_units = Decimal("142.857142857142857143")
    assert result["units"] == expected_units
    assert con.execute("""SELECT units_original FROM mf_paper_lots
        WHERE account_id='paper-1' AND lot_id='mf-purchase-lot:purchase-1'""").fetchone()[0] == expected_units
    assert account_snapshot(con, account_id="paper-1")["cash_inr"] == Decimal("9000")
    assert ledger_snapshot(con, account_id="paper-1")["is_reconciled"] is True
    replay = apply_allotment(
        con, order_id=order.order_id, nav_observation_id="eligible-nav", nav_per_unit=NavPerUnit(7),
        terms=terms, permissions=permissions, decision_at=decision,
        allotted_at=decision + timedelta(minutes=2), now=decision + timedelta(minutes=3),
    )
    assert replay["status"] == "already_allotted"
    assert con.execute("SELECT COUNT(*) FROM mf_paper_lots WHERE account_id='paper-1'").fetchone()[0] == 1


def test_observed_payment_milestone_persists_permission_evidence(setup_purchase):
    con, _existing, calendar, _permissions, _terms = setup_purchase
    order = MFOrder(
        order_id="purchase-observed", account_id="paper-1", folio_id="SYNTHETIC-1",
        scheme_code="123456", plan="DIRECT", option="GROWTH",
        transaction_type=MFOrderKind.PURCHASE, requested_at=REQUESTED,
        amount_inr=Decimal("500"),
    )
    submit_order(con, order, valid_business_days=2, calendar=calendar,
                 now=REQUESTED + timedelta(seconds=2))
    permission = DataPermission(
        source_id="BANK", use=DataUse.PAPER_SIMULATION,
        status=PermissionStatus.USE_ALLOWED, effective_from=date(2026, 11, 23),
        effective_through=None, evidence_ref="bank-use-approval", reviewer="owner",
    )
    effective = REQUESTED + timedelta(minutes=2)
    milestone = MFPurchasePaymentMilestone(
        PaymentMilestoneKind.INITIATED, effective, effective + timedelta(seconds=1),
        PaymentEvidenceBasis.OBSERVED_EVIDENCE, source_id="BANK",
        source_ref="payment-initiation-1", source_sha256="b" * 64,
    )
    recorded = record_payment_milestone(
        con, order_id=order.order_id, milestone=milestone,
        permissions=(permission,), now=milestone.received_at,
    )
    row = con.execute("""SELECT permission_evidence_json, event_sha256
        FROM mf_paper_purchase_payment_events WHERE order_id=?""", [order.order_id]).fetchone()
    assert "bank-use-approval" in row[0]
    assert row[1] == recorded["event_sha256"]
    assert record_payment_milestone(
        con, order_id=order.order_id, milestone=milestone,
        permissions=(permission,), now=milestone.received_at,
    )["status"] == "already_recorded"
