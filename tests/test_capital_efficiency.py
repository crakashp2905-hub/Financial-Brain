"""Canonical-ledger capital efficiency accounting and denominator refusal."""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from financial_brain.portfolio.canonical_ledger import AssetClass, CanonicalPortfolioLedger, EventKind, PortfolioEvent
from financial_brain.portfolio.capital_efficiency import CapitalEfficiencyError, report_capital_efficiency

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 7, 1, tzinfo=timezone.utc)
SHA = "a" * 64


def event(event_id, kind, at, **fields):
    return PortfolioEvent("paper", "swing", "test", event_id, SHA, kind,
                          at.isoformat(), asset_class=fields.pop("asset_class", None),
                          instrument_id=fields.pop("instrument_id", None),
                          side=fields.pop("side", None), quantity=fields.pop("quantity", None),
                          price=fields.pop("price", None), cash_delta=fields.pop("cash_delta", None),
                          metadata=fields.pop("metadata", None), **fields)


def populated_ledger(path):
    ledger = CanonicalPortfolioLedger(path)
    ledger.import_events([
        event("total", EventKind.CASH_FLOW, START, cash_delta="100000",
              metadata={"cash_flow_type": "OPENING_BALANCE", "capital_type": "TOTAL"}),
        event("deployed", EventKind.CASH_FLOW, START, cash_delta="50000",
              metadata={"cash_flow_type": "OPENING_BALANCE", "capital_type": "DEPLOYED"}),
        event("buy", EventKind.FILL, START.replace(day=10), asset_class=AssetClass.EQUITY,
              instrument_id="NSE:ABC", side="BUY", quantity="10", price="100"),
        event("fee", EventKind.FEE, START.replace(day=10), cash_delta="-10"),
        event("sell", EventKind.FILL, START.replace(day=20), asset_class=AssetClass.EQUITY,
              instrument_id="NSE:ABC", side="SELL", quantity="10", price="112"),
    ])
    return ledger


def test_report_measures_risk_cash_turnover_and_both_capital_returns(tmp_path):
    ledger = populated_ledger(tmp_path / "ledger.sqlite3")
    report = report_capital_efficiency(ledger=ledger, account_id="paper", start=START, end=END,
                                       risk_budget_inr=Decimal("1000"))
    assert report.net_pnl_inr == Decimal("110")
    assert report.net_rupees_per_rupee_risk == Decimal("0.11")
    assert report.maximum_concurrent_cash_use_inr == Decimal("1010")
    assert report.annualized_turnover_inr == pytest.approx(Decimal("4275.1381215"))
    assert report.deployed_capital_return == Decimal("0.0022")
    assert report.total_capital_return == Decimal("0.0011")
    assert len(report.source_sha256) == len(report.report_sha256) == 64


def test_report_refuses_missing_opening_denominators(tmp_path):
    ledger = CanonicalPortfolioLedger(tmp_path / "empty.sqlite3")
    with pytest.raises(CapitalEfficiencyError, match="no canonical"):
        report_capital_efficiency(ledger=ledger, account_id="paper", start=START, end=END,
                                  risk_budget_inr=100)


def test_report_refuses_unresolved_positions(tmp_path):
    ledger = populated_ledger(tmp_path / "ledger.sqlite3")
    ledger.append(event("unresolved", EventKind.UNRESOLVED_POSITION, END - __import__('datetime').timedelta(days=1),
                        instrument_id="NSE:OLD", quantity="1"))
    with pytest.raises(CapitalEfficiencyError, match="unresolved positions"):
        report_capital_efficiency(ledger=ledger, account_id="paper", start=START, end=END,
                                  risk_budget_inr=100)


def test_report_refuses_unmarked_open_inventory(tmp_path):
    ledger = CanonicalPortfolioLedger(tmp_path / "ledger.sqlite3")
    ledger.import_events([
        event("total", EventKind.CASH_FLOW, START, cash_delta="100000",
              metadata={"cash_flow_type": "OPENING_BALANCE", "capital_type": "TOTAL"}),
        event("deployed", EventKind.CASH_FLOW, START, cash_delta="50000",
              metadata={"cash_flow_type": "OPENING_BALANCE", "capital_type": "DEPLOYED"}),
        event("buy", EventKind.FILL, START.replace(day=10), asset_class=AssetClass.EQUITY,
              instrument_id="NSE:ABC", side="BUY", quantity="10", price="100"),
    ])
    with pytest.raises(CapitalEfficiencyError, match="lacks an interval-end mark"):
        report_capital_efficiency(ledger=ledger, account_id="paper", start=START, end=END,
                                  risk_budget_inr=100)
