"""Canonical accounting import for durable mutual-fund paper events."""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from financial_brain.portfolio.canonical_ledger import CanonicalPortfolioLedger
from financial_brain.portfolio.canonical_ledger import EventKind, PortfolioEvent
from financial_brain.portfolio.mutual_fund_adapter import (
    MutualFundImportError,
    import_mutual_fund_rows,
)


HASH_SUBSCRIBE = "1" * 64
HASH_REDEEM = "2" * 64


def mf_row(event_id: str, kind: str, **overrides):
    row = {
        "event_id": event_id,
        "event_sha256": HASH_SUBSCRIBE if kind == "SUBSCRIBE" else HASH_REDEEM,
        "kind": kind,
        "scheme_code": "123456",
        "plan": "DIRECT",
        "option": "GROWTH",
        "isin": "INF000000001",
        "event_date": date(2026, 11, 20),
        "nav_per_unit": Decimal("100") if kind == "SUBSCRIBE" else Decimal("110"),
        "units": Decimal("9.99") if kind == "SUBSCRIBE" else Decimal("1"),
        "gross_inr": Decimal("1000") if kind == "SUBSCRIBE" else Decimal("110"),
        "exit_load_inr": Decimal("0") if kind == "SUBSCRIBE" else Decimal("2"),
        "other_charges_inr": Decimal("1") if kind == "SUBSCRIBE" else Decimal("1"),
        "net_inr": Decimal("999") if kind == "SUBSCRIBE" else Decimal("107"),
        "created_at": datetime(2026, 11, 21, 12, tzinfo=timezone.utc),
        "folio_id": "folio-paper-1",
    }
    row.update(overrides)
    return row


def test_mf_subscribe_and_redeem_rows_import_as_costed_canonical_events(tmp_path):
    ledger = CanonicalPortfolioLedger(tmp_path / "mf-canonical.sqlite")
    ledger.append(PortfolioEvent(
        account_id="paper-main", sleeve_id="MUTUAL_FUND", source_system="paper-setup",
        source_event_id="opening-cash", source_sha256="3" * 64,
        event_kind=EventKind.CASH_FLOW, occurred_at="2026-11-19T00:00:00+00:00",
        cash_delta="1000", metadata={"cash_flow_type": "OPENING_BALANCE"},
    ))
    rows = [mf_row("purchase-1", "SUBSCRIBE"), mf_row("redemption-1", "REDEEM")]

    assert import_mutual_fund_rows(rows, ledger, account_id="paper-main") == {
        "received": 5, "added": 5, "already_present": 0,
    }
    assert import_mutual_fund_rows(rows, ledger, account_id="paper-main") == {
        "received": 5, "added": 0, "already_present": 5,
    }

    snapshot = ledger.snapshot("paper-main")
    position = snapshot["positions"][0]
    assert position["asset_class"] == "MUTUAL_FUND"
    assert position["quantity"] == Decimal("8.99")
    assert position["average_cost"] == Decimal("100")
    assert position["realized_pnl"] == Decimal("10")
    assert snapshot["cash"]["INR"] == Decimal(0)
    assert snapshot["cash_receivables"] == [
        {"currency": "INR", "receivable_id": "redemption-1", "amount": Decimal("107")},
    ]
    assert sum(Decimal(event["cash_delta"]) for event in ledger.events("paper-main")
               if event["event_kind"] == "FEE") == Decimal("-4")

    payout = {
        "redemption_event_id": "redemption-1",
        "settlement_id": "settlement-1",
        "net_inr": Decimal("107"),
        "payout_available_at": datetime(2026, 11, 25, 10, tzinfo=timezone.utc),
        "payout_source_id": "RTA",
        "payout_evidence_ref": "rta:payment:1",
        "payout_source_sha256": "4" * 64,
    }
    assert import_mutual_fund_rows(rows, ledger, account_id="paper-main", payout_rows=[payout]) == {
        "received": 6, "added": 1, "already_present": 5,
    }
    settled = ledger.snapshot("paper-main")
    assert settled["cash"]["INR"] == Decimal("107")
    assert settled["cash_receivables"] == []


def test_mf_import_rejects_unreconciled_units_nav_and_costs(tmp_path):
    ledger = CanonicalPortfolioLedger(tmp_path / "mf-invalid.sqlite")
    with pytest.raises(MutualFundImportError, match="allotted MF units times NAV"):
        import_mutual_fund_rows(
            [mf_row("bad-purchase", "SUBSCRIBE", units=Decimal("10"))],
            ledger, account_id="paper-main",
        )
    assert ledger.event_count() == 0


def test_mf_import_refuses_unhashed_or_naive_source_rows(tmp_path):
    row = mf_row("bad-hash", "SUBSCRIBE", event_sha256="not-a-hash")
    with pytest.raises(MutualFundImportError, match="lowercase SHA-256"):
        import_mutual_fund_rows([row], CanonicalPortfolioLedger(tmp_path / "bad-hash.sqlite"),
                                account_id="paper")

    row = mf_row("naive-time", "SUBSCRIBE", created_at=datetime(2026, 11, 21, 12))
    with pytest.raises(MutualFundImportError, match="timezone-aware"):
        import_mutual_fund_rows([row], CanonicalPortfolioLedger(tmp_path / "naive-time.sqlite"),
                                account_id="paper")
