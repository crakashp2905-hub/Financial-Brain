from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from financial_brain.paper.derivatives_subledger import (
    DerivativesLedgerError, DerivativesSubledger, OptionContract, OptionLegOrder,
)
from financial_brain.portfolio.canonical_ledger import CanonicalPortfolioLedger
from financial_brain.portfolio.derivatives_adapter import import_derivatives_package

NOW = datetime(2026, 11, 20, 10, tzinfo=timezone.utc)
EXPIRY = date(2026, 11, 26)
HASH = "a" * 64
PUBLISHED = datetime(2026, 11, 27, 10, tzinfo=timezone.utc)
EXPIRY_CUTOFF = datetime(2026, 11, 26, 15, 30, tzinfo=timezone.utc)


def _contract(contract_id: str, underlying: str, strike: int, option_type: str, settlement: str
              ) -> OptionContract:
    return OptionContract(contract_id, underlying, option_type, Decimal(str(strike)), EXPIRY,
                          50, settlement, HASH)


def _open_spread(db: DerivativesSubledger, *, settlement: str, put: bool = False) -> None:
    underlying = "RELIANCE" if settlement == "PHYSICAL" else "NIFTY"
    option_type = "PE" if put else "CE"
    strikes = (110, 100) if put else (100, 110)
    legs = (
        OptionLegOrder("long", _contract(f"{underlying}-{strikes[0]}-{option_type}", underlying,
                                          strikes[0], option_type, settlement), "BUY", 50),
        OptionLegOrder("short", _contract(f"{underlying}-{strikes[1]}-{option_type}", underlying,
                                           strikes[1], option_type, settlement), "SELL", 50),
    )
    db.create_package(package_id="spread", account_id="paper-account-1", structure="DEBIT_VERTICAL",
                      legs=legs, created_at=NOW - timedelta(days=1))
    for leg, price in zip(legs, ("10", "4"), strict=True):
        db.record_fill(package_id="spread", event_id=f"fill-{leg.leg_id}", leg_id=leg.leg_id,
                       side=leg.side, quantity=50, price=Decimal(price), occurred_at=NOW,
                       quote_event_id=f"quote-{leg.leg_id}", quote_observed_at=NOW - timedelta(seconds=2),
                       quote_available_at=NOW - timedelta(seconds=1), quote_evidence_sha256=HASH)


def _settle_spread(db: DerivativesSubledger, *, settlement: str, put: bool = False) -> None:
    underlying = "RELIANCE" if settlement == "PHYSICAL" else "NIFTY"
    contracts = ([(f"{underlying}-110-PE", Decimal("90")),
                  (f"{underlying}-100-PE", Decimal("90"))] if put else
                 [(f"{underlying}-100-CE", Decimal("115")),
                  (f"{underlying}-110-CE", Decimal("115"))])
    for index, (contract_id, spot) in enumerate(contracts):
        db.settle_expiry(package_id="spread", contract_id=contract_id,
                         event_id=f"settle-{index}", settlement_date=EXPIRY,
                         expiry_cutoff_at=EXPIRY_CUTOFF, expiry_calendar_sha256="c" * 64,
                         settlement_at=PUBLISHED + timedelta(minutes=1), published_at=PUBLISHED,
                         underlying_price=spot, source_id="fixture-settlement", source_sha256="b" * 64)


@pytest.mark.parametrize(("settlement", "put"), [("CASH", False), ("PHYSICAL", False), ("PHYSICAL", True)])
def test_derivatives_import_reconciles_fills_cash_and_expiry_positions(tmp_path: Path,
                                                                       settlement: str, put: bool) -> None:
    subledger = DerivativesSubledger(tmp_path / "derivatives.sqlite")
    _open_spread(subledger, settlement=settlement, put=put)
    _settle_spread(subledger, settlement=settlement, put=put)
    canonical = CanonicalPortfolioLedger(tmp_path / "canonical.sqlite")

    first = import_derivatives_package(subledger, canonical, "spread")
    second = import_derivatives_package(subledger, canonical, "spread")
    account = canonical.snapshot("paper-account-1")

    assert first["received"] > 0 and first["added"] == first["received"]
    assert second["added"] == 0 and second["already_present"] == first["received"]
    assert account["cash"] == {"INR": Decimal("200")}
    assert sum((row["realized_pnl"] for row in account["positions"]), Decimal(0)) == Decimal("200")
    assert all(row["quantity"] == 0 for row in account["positions"])
    if settlement == "PHYSICAL":
        underlying = next(row for row in account["positions"] if row["asset_class"] == "EQUITY")
        assert underlying["mark"] == Decimal("115" if not put else "90")
        assert underlying["quantity"] == 0
    subledger.close()


def test_adapter_refuses_a_corrupt_derivative_source_chain(tmp_path: Path) -> None:
    subledger = DerivativesSubledger(tmp_path / "derivatives.sqlite")
    _open_spread(subledger, settlement="CASH")
    canonical = CanonicalPortfolioLedger(tmp_path / "canonical.sqlite")
    subledger._con.execute("UPDATE derivative_events SET event_sha256=? WHERE event_id=?", ["f" * 64, "fill-long"])
    with pytest.raises(DerivativesLedgerError, match="hash chain"):
        import_derivatives_package(subledger, canonical, "spread")
    assert canonical.event_count() == 0
    subledger.close()
