from datetime import datetime, timedelta, timezone
from decimal import Decimal

from financial_brain.paper.mf_lot_book import create_account
from financial_brain.paper.mf_paper_realism_book import assess_account_mf_realism
from financial_brain.storage.db import Database
from financial_brain.config import Config


NOW = datetime(2026, 11, 23, 8, tzinfo=timezone.utc)
SHA = "a" * 64


def test_empty_account_has_zero_score_and_all_dimensions_missing(tmp_path):
    database = Database(Config(data_root=tmp_path).ensure())
    database.migrate()
    with database.connect() as con:
        create_account(con, "realism-empty", 100, now=NOW)
        assessment = assess_account_mf_realism(
            con, account_id="realism-empty", assessed_at=NOW,
        )
    assert assessment.score == 0
    assert assessment.evidence_coverage == 0
    assert assessment.observed_coverage == 0
    assert len(assessment.missing_dimensions) == 4


def test_account_score_uses_asof_orders_payments_terms_and_settlement_state(tmp_path):
    database = Database(Config(data_root=tmp_path).ensure())
    database.migrate()
    with database.connect() as con:
        create_account(con, "realism-full", 1000, now=NOW - timedelta(days=2))
        requested = NOW - timedelta(hours=3)
        accepted = NOW - timedelta(hours=2)
        con.execute("""INSERT INTO mf_paper_orders (
            order_id, account_id, folio_id, scheme_code, plan, option, transaction_type,
            requested_at, amount_inr, units, request_basis, request_sha256, valid_through,
            current_state, created_at, updated_at)
            VALUES ('purchase-1', 'realism-full', 'folio-1', 'scheme-1', 'DIRECT',
            'GROWTH', 'PURCHASE', ?, 100, NULL, 'SIMULATED_ASSUMPTION', ?,
            DATE '2026-11-24', 'ACCEPTED', ?, ?)""",
            [requested, SHA, requested, accepted])
        con.execute("""INSERT INTO mf_paper_order_transitions (
            order_id, sequence, state, effective_at, received_at, basis,
            event_sha256, evidence_json)
            VALUES ('purchase-1', 1, 'REQUESTED', ?, ?, 'SIMULATED_ASSUMPTION',
                    'request-hash', ?),
                   ('purchase-1', 2, 'ACCEPTED', ?, ?, 'SIMULATED_ASSUMPTION',
                    'accept-hash', '{}')""",
            [requested, requested, '{"request_sha256":"' + SHA + '"}',
             accepted, accepted])
        con.execute("""INSERT INTO mf_paper_purchase_eligibility (
            order_id, state, eligible_nav_date, transaction_at, decision_at,
            evidence_json, eligibility_sha256, nav_observation_id, nav_per_unit,
            units_allotted, allotment_sha256, created_at, updated_at)
            VALUES ('purchase-1', 'ALLOTTED', DATE '2026-11-23', ?, ?, '{}', ?,
                    'nav-1', 10, 10, ?, ?, ?)""",
            [accepted, accepted, SHA, SHA, accepted, accepted])
        for sequence, kind in enumerate((
            "INITIATED", "CREDITED_TO_SCHEME", "AVAILABLE_FOR_UTILIZATION",
        ), start=1):
            effective = NOW - timedelta(minutes=20 - sequence)
            con.execute("""INSERT INTO mf_paper_purchase_payment_events (
                order_id, sequence, milestone_kind, effective_at, received_at, basis,
                event_sha256) VALUES ('purchase-1', ?, ?, ?, ?, 'SIMULATED_ASSUMPTION', ?)""",
                [sequence, kind, effective, effective, f"payment-hash-{sequence}"])
        con.execute("""INSERT INTO mf_scheme_commercial_terms (
            record_id, scheme_code, plan, option, isin, effective_from, effective_to,
            ter_percent, aum_inr, exit_loads_json, charge_schedule_json,
            minimum_initial_inr, minimum_additional_inr, minimum_redemption_inr,
            exit_restrictions_json, source_id, source_ref, source_sha256,
            published_at, observed_at)
            VALUES ('terms-1', 'scheme-1', 'DIRECT', 'GROWTH', 'isin-1',
            DATE '2026-01-01', NULL, NULL, NULL, '[]', '[]', '0', '0', '0', '[]',
            'AMC', 'terms-ref', ?, ?, ?)""", [SHA, NOW - timedelta(days=30), NOW - timedelta(days=30)])
        con.execute("""INSERT INTO mf_paper_lots (
            account_id, lot_id, folio_id, scheme_code, plan, option, isin, allotted_on,
            units_original, units_remaining, purchase_nav, exit_loads_json,
            exit_loads_sha256, subscription_event_id, created_at)
            VALUES ('realism-full', 'lot-1', 'folio-1', 'scheme-1', 'DIRECT',
            'GROWTH', 'isin-1', DATE '2026-11-23', 10, 10, 10, '[]', ?, 'event-1', ?)""",
            [SHA, accepted])
        con.execute("""INSERT INTO mf_paper_redemption_settlements (
            account_id, settlement_id, folio_id, scheme_code, plan, option, isin,
            requested_units, requested_on, requested_at, state, eligible_nav_date,
            eligibility_sha256, request_sha256, created_at, updated_at)
            VALUES ('realism-full', 'redeem-1', 'folio-1', 'scheme-1', 'DIRECT',
            'GROWTH', 'isin-1', 1, DATE '2026-11-23', ?, 'PAYOUT_PENDING',
            DATE '2026-11-23', ?, ?, ?, ?)""",
            [requested, SHA, SHA, requested, NOW - timedelta(minutes=1)])

        assessment = assess_account_mf_realism(
            con, account_id="realism-full", assessed_at=NOW,
        )
        con.execute("""UPDATE mf_paper_redemption_settlements SET state='CASH_AVAILABLE',
            payout_available_at=?, payout_basis='OBSERVED_EVIDENCE',
            payout_evidence_ref='bank-statement:42', payout_source_id='BANK',
            payout_source_sha256=?, payout_sha256=?, updated_at=?
            WHERE account_id='realism-full' AND settlement_id='redeem-1'""",
            [NOW, SHA, SHA, NOW])
        paid_assessment = assess_account_mf_realism(
            con, account_id="realism-full", assessed_at=NOW,
        )
        earlier = assess_account_mf_realism(
            con, account_id="realism-full", assessed_at=requested - timedelta(seconds=1),
        )

    factors = {factor.dimension.value: factor for factor in assessment.factors}
    assert assessment.score == Decimal("95.00")
    assert assessment.evidence_coverage == Decimal("100.00")
    assert assessment.observed_coverage == Decimal("20.00")
    assert factors["ELIGIBLE_NAV_CERTAINTY"].value == 1
    assert factors["PAYMENT_TIME_EVIDENCE"].value == 1
    assert factors["LOAD_COMPLETENESS"].basis.value == "OBSERVED_EVIDENCE"
    assert factors["REDEMPTION_SETTLEMENT_CONFIDENCE"].value == Decimal("0.75")
    paid_factors = {factor.dimension.value: factor for factor in paid_assessment.factors}
    assert paid_factors["REDEMPTION_SETTLEMENT_CONFIDENCE"].value == 1
    assert paid_factors["REDEMPTION_SETTLEMENT_CONFIDENCE"].basis.value == "OBSERVED_EVIDENCE"
    assert paid_assessment.observed_coverage == Decimal("40.00")
    assert earlier.score == 0
    assert len(earlier.missing_dimensions) == 4
