from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from financial_brain.config import Config
from financial_brain.forward_edge.data_permissions import (
    DataPermission, DataUse, PermissionStatus,
)
from financial_brain.ingest.mf_scheme_flows import persist as persist_flow
from financial_brain.ingest.mf_scheme_terms import persist as persist_terms
from financial_brain.paper.mf_flow_book import apply_economic_flow
from financial_brain.paper.mf_lot_book import account_snapshot, create_account, subscribe
from financial_brain.providers.mf_scheme_flows import (
    SchemeEconomicFlow, SchemeFlowKind,
)
from financial_brain.providers.mf_scheme_terms import ExitRestriction, SchemeCommercialTerms
from financial_brain.storage.db import Database

SOURCE = "AMC"
FLOW_HASH = "f" * 64
TERMS_HASH = "a" * 64
FLOW_PUBLISHED = datetime(2026, 1, 4, tzinfo=timezone.utc)
DECISION = datetime(2026, 1, 5, 12, tzinfo=timezone.utc)


def _permissions(*uses):
    return tuple(DataPermission(
        source_id=SOURCE, use=use, status=PermissionStatus.USE_ALLOWED,
        effective_from=date(2026, 1, 1), effective_through=None,
        evidence_ref="approved-amc-paper-terms", reviewer="data-owner",
    ) for use in uses)


PERMISSIONS = _permissions(
    DataUse.RESEARCH, DataUse.RETENTION, DataUse.PAPER_SIMULATION,
)


def _terms(*, scheme_code="100001", isin="INF000000001", digest=TERMS_HASH):
    return SchemeCommercialTerms(
        scheme_code=scheme_code, plan="DIRECT", option="GROWTH", isin=isin,
        effective_from=date(2026, 1, 1), effective_to=None,
        ter_percent=None, aum_inr=None, exit_loads=(),
        minimum_initial_inr=None, minimum_additional_inr=None,
        exit_restrictions=(), source_id=SOURCE, source_ref="scheme-terms-2026-01",
        source_sha256=digest, published_at=datetime(2026, 1, 1, 8, tzinfo=timezone.utc),
        observed_at=datetime(2026, 1, 1, 9, tzinfo=timezone.utc),
    )


def _flow(kind, **changes):
    values = {
        "scheme_code": "100001", "isin": "INF000000001", "kind": kind,
        "effective_date": date(2026, 1, 3), "cash_per_unit": None,
        "reinvestment_nav": None, "unit_ratio": None,
        "successor_scheme_code": None, "successor_isin": None,
        "source_id": SOURCE, "source_ref": "amc-circular-2026-01",
        "source_sha256": FLOW_HASH, "published_at": FLOW_PUBLISHED,
        "observed_at": datetime(2026, 1, 4, 1, tzinfo=timezone.utc),
    }
    values.update(changes)
    return SchemeEconomicFlow(**values)


@pytest.fixture
def book(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    with db.connect() as con:
        create_account(con, "paper-1", 1000, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        terms = _terms()
        persist_terms(con, terms, permissions=PERMISSIONS)
        subscribe(
            con, account_id="paper-1", event_id="sub-1", lot_id="lot-1",
            scheme_code=terms.scheme_code, plan=terms.plan, option=terms.option,
            isin=terms.isin, allotted_on=date(2026, 1, 2), investment_inr=1000,
            nav_per_unit=100, exit_loads=terms.exit_loads,
            exit_loads_sha256=terms.source_sha256, terms=terms,
            permissions=PERMISSIONS, decision_at=datetime(2026, 1, 2, 12,
                                                           tzinfo=timezone.utc),
        )
        yield con


def _apply(con, flow, *, nav_after=90, **kwargs):
    persist_flow(con, flow, permissions=PERMISSIONS)
    return apply_economic_flow(
        con, account_id="paper-1", flow=flow, decision_at=DECISION,
        nav_before=100, nav_after=nav_after, permissions=PERMISSIONS,
        now=datetime(2026, 1, 6, 12, tzinfo=timezone.utc), **kwargs,
    )


def test_idcw_payout_updates_canonical_cash_and_is_idempotent(book):
    flow = _flow(SchemeFlowKind.IDCW_PAYOUT, cash_per_unit=10)
    applied = _apply(book, flow)
    assert applied["value_before_inr"] == applied["value_after_inr"] == Decimal("1000.00")
    assert applied["total_return_percent"] == 0
    assert account_snapshot(book, "paper-1")["cash_inr"] == Decimal("100")
    journal = book.execute(
        "SELECT flow_kind, cash_delta_inr, units_before, units_after "
        "FROM mf_paper_flow_events WHERE account_id=? AND source_flow_id=?",
        ["paper-1", flow.event_id],
    ).fetchone()
    assert journal == ("IDCW_PAYOUT", Decimal("100"), Decimal("10"), Decimal("10"))

    replay = apply_economic_flow(
        book, account_id="paper-1", flow=flow, decision_at=DECISION,
        nav_before=100, nav_after=90, permissions=PERMISSIONS,
    )
    assert replay["status"] == "already_applied"
    assert account_snapshot(book, "paper-1")["cash_inr"] == Decimal("100")
    with pytest.raises(ValueError, match="different inputs"):
        apply_economic_flow(
            book, account_id="paper-1", flow=flow, decision_at=DECISION,
            nav_before=100, nav_after=91, permissions=PERMISSIONS,
        )


def test_idcw_reinvestment_creates_an_independently_dated_lot(book):
    flow = _flow(SchemeFlowKind.IDCW_REINVESTMENT,
                 cash_per_unit=10, reinvestment_nav=90)
    terms = _terms()
    persist_terms(book, terms, permissions=PERMISSIONS)
    result = _apply(book, flow, reinvestment_lot_id="idcw-lot-1",
                    reinvestment_terms=terms)
    position = account_snapshot(book, "paper-1")["positions"][0]
    assert position["units"] == Decimal("11.111111111111111111")
    assert result["value_before_inr"] == result["value_after_inr"] == Decimal("1000.00")
    lot = book.execute(
        "SELECT allotted_on, purchase_nav, subscription_event_id FROM mf_paper_lots "
        "WHERE account_id=? AND lot_id=?", ["paper-1", "idcw-lot-1"],
    ).fetchone()
    assert lot == (date(2026, 1, 3), Decimal("90"), f"MF_FLOW:{flow.event_id}")
    assert position["cost_basis_inr"] == Decimal("1099.999999999999999990")
    journal = book.execute(
        "SELECT flow_kind, cash_delta_inr, units_before, units_after, "
        "reinvestment_lot_id FROM mf_paper_flow_events "
        "WHERE account_id=? AND source_flow_id=?",
        ["paper-1", flow.event_id],
    ).fetchone()
    assert journal == (
        "IDCW_REINVESTMENT", Decimal("0.000000000000000010"), Decimal("10"),
        Decimal("11.111111111111111111"), "idcw-lot-1",
    )


def test_unit_split_adjusts_open_lots_without_changing_cost_basis(book):
    flow = _flow(SchemeFlowKind.UNIT_SPLIT, unit_ratio=2)
    result = _apply(book, flow, nav_after=50)
    position = account_snapshot(book, "paper-1")["positions"][0]
    assert position["units"] == Decimal("20")
    assert position["cost_basis_inr"] == Decimal("1000")
    assert result["value_before_inr"] == result["value_after_inr"] == Decimal("1000.00")


def test_merger_transfers_lots_only_with_known_successor_terms(book):
    flow = _flow(
        SchemeFlowKind.SCHEME_MERGER, unit_ratio=Decimal("0.5"),
        successor_scheme_code="200002", successor_isin="INF000000002",
    )
    successor = _terms(scheme_code="200002", isin="INF000000002", digest="b" * 64)
    persist_terms(book, successor, permissions=PERMISSIONS)
    result = _apply(book, flow, nav_after=200, successor_terms=successor)
    position = account_snapshot(book, "paper-1")["positions"][0]
    assert (position["scheme_code"], position["isin"]) == (
        "200002", "INF000000002",
    )
    assert position["units"] == Decimal("5")
    assert position["cost_basis_inr"] == Decimal("1000")
    assert result["total_return_percent"] == 0


def test_missing_paper_permission_leaves_lots_cash_and_flow_journal_unchanged(book):
    flow = _flow(SchemeFlowKind.IDCW_PAYOUT, cash_per_unit=10)
    persist_flow(book, flow, permissions=PERMISSIONS)
    before = account_snapshot(book, "paper-1")
    with pytest.raises(ValueError, match="PAPER_SIMULATION"):
        apply_economic_flow(
            book, account_id="paper-1", flow=flow, decision_at=DECISION,
            nav_before=100, nav_after=90,
            permissions=_permissions(DataUse.RESEARCH, DataUse.RETENTION),
        )
    assert account_snapshot(book, "paper-1") == before
    assert book.execute("SELECT count(*) FROM mf_paper_flow_events").fetchone()[0] == 0


def test_reinvestment_requires_terms_and_elss_lock_in(book):
    flow = _flow(SchemeFlowKind.IDCW_REINVESTMENT,
                 cash_per_unit=10, reinvestment_nav=90)
    elss_terms = replace(_terms(), exit_restrictions=(ExitRestriction.ELSS_LOCK_IN,))
    persist_flow(book, flow, permissions=PERMISSIONS)
    with pytest.raises(ValueError, match="explicit lock-in"):
        apply_economic_flow(
            book, account_id="paper-1", flow=flow, decision_at=DECISION,
            nav_before=100, nav_after=90, permissions=PERMISSIONS,
            reinvestment_lot_id="elss-lot", reinvestment_terms=elss_terms,
        )
    assert book.execute("SELECT count(*) FROM mf_paper_flow_events").fetchone()[0] == 0

