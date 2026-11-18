from datetime import date
from decimal import Decimal

import pytest

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.kernel.options_paper import OptionContract, OptionsPaperAccount, OptionsPaperError


HASH_A = "a" * 64
HASH_B = "b" * 64
EXPIRY = date(2026, 11, 27)


def option(contract_id: str, kind: str, strike: str, settlement: str = "PHYSICAL") -> OptionContract:
    return OptionContract(
        contract_id=contract_id, underlying="ACME", kind=kind, strike=Decimal(strike),
        expiry=EXPIRY, lot_size=10, settlement=settlement,
        short_margin_per_lot=Decimal("500"), source_id="fixture-contract-master",
        source_sha256=HASH_A,
    )


def test_physical_call_spread_expiry_reconciles_both_legs_margin_cash_and_restart(tmp_path):
    path = tmp_path / "options.jsonl"
    account = OptionsPaperAccount(path, account_id="opts", initial_cash=Decimal("10000"))
    long_call = option("ACME-C110", "CALL", "110")
    short_call = option("ACME-C100", "CALL", "100")

    # Buying the protective leg first keeps the short leg inside the defined-risk spread.
    account.trade("buy-protection", long_call, quantity_lots=1, premium=Decimal("2"),
                  session_date=date(2026, 11, 13), source_sha256=HASH_B)
    account.trade("sell-call", short_call, quantity_lots=-1, premium=Decimal("8"),
                  session_date=date(2026, 11, 13), source_sha256=HASH_B)
    account.trade("sell-call", short_call, quantity_lots=-1, premium=Decimal("8"),
                  session_date=date(2026, 11, 13), source_sha256=HASH_B)

    assert account.cash == Decimal("10060.00")
    assert account.margin_required == Decimal("100.00")
    assert account.available_cash == Decimal("9960.00")
    assert account.option_positions == {"ACME-C110": 1, "ACME-C100": -1}

    account.settle_expiry("expiry", settlement_prices={"ACME": Decimal("120")},
                          session_date=EXPIRY, source_sha256=HASH_B)
    expiry_records = len(DurableJournal(path).records())
    account.settle_expiry("expiry", settlement_prices={"ACME": Decimal("120")},
                          session_date=EXPIRY, source_sha256=HASH_B)
    assert len(DurableJournal(path).records()) == expiry_records
    assert account.cash == Decimal("9960.00")
    assert account.underlying_positions == {}
    assert account.option_positions == {}
    assert account.margin_required == Decimal("0")
    assert account.realized_pnl == Decimal("-40.00")
    assert account.equity == Decimal("9960.00")
    assert account.ledger.trial_balance_ok()

    recovered = OptionsPaperAccount(path, account_id="opts", initial_cash=Decimal("10000"))
    assert (recovered.cash, recovered.realized_pnl, recovered.equity) == (
        account.cash, account.realized_pnl, account.equity
    )
    assert recovered.ledger.trial_balance_ok()


def test_cash_settled_long_put_pays_intrinsic_and_physical_assignment_creates_shares(tmp_path):
    cash_path = tmp_path / "cash-options.jsonl"
    cash_account = OptionsPaperAccount(cash_path, account_id="cash", initial_cash=Decimal("10000"))
    long_put = option("ACME-P120", "PUT", "120", settlement="CASH")
    cash_account.trade("buy-put", long_put, quantity_lots=2, premium=Decimal("1"),
                       session_date=date(2026, 11, 13), source_sha256=HASH_B)
    cash_account.settle_expiry("cash-expiry", settlement_prices={"ACME": Decimal("100")},
                               session_date=EXPIRY, source_sha256=HASH_B)
    assert cash_account.cash == Decimal("10380.00")
    assert cash_account.realized_pnl == Decimal("380.00")
    assert cash_account.underlying_positions == {}

    physical_path = tmp_path / "physical-options.jsonl"
    physical = OptionsPaperAccount(physical_path, account_id="physical", initial_cash=Decimal("10000"))
    short_put = option("ACME-P120-PHYS", "PUT", "120")
    physical.trade("sell-put", short_put, quantity_lots=-1, premium=Decimal("3"),
                   session_date=date(2026, 11, 13), source_sha256=HASH_B)
    assert physical.margin_required == Decimal("500")
    physical.settle_expiry("put-assignment", settlement_prices={"ACME": Decimal("100")},
                           session_date=EXPIRY, source_sha256=HASH_B)
    assert physical.cash == Decimal("8830.00")
    assert physical.underlying_positions == {"ACME": 10}
    assert physical.equity == Decimal("9830.00")
    assert physical.realized_pnl == Decimal("-170.00")
    assert physical.ledger.trial_balance_ok()


def test_naked_short_margin_gate_and_expiry_data_validation_are_fail_closed(tmp_path):
    path = tmp_path / "options.jsonl"
    account = OptionsPaperAccount(path, account_id="opts", initial_cash=Decimal("600"))
    short = option("ACME-C100-NAKED", "CALL", "100")
    account.trade("sell-one", short, quantity_lots=-1, premium=Decimal("0"),
                  session_date=date(2026, 11, 13), source_sha256=HASH_B)
    assert account.margin_required == Decimal("500")
    assert account.available_cash == Decimal("100")

    with pytest.raises(OptionsPaperError, match="insufficient cash"):
        account.trade("sell-too-many", short, quantity_lots=-1, premium=Decimal("0"),
                      session_date=date(2026, 11, 13), source_sha256=HASH_B)
    before = len(DurableJournal(path).records())
    with pytest.raises(OptionsPaperError, match="settlement prices"):
        account.settle_expiry("missing-spot", settlement_prices={}, session_date=EXPIRY,
                              source_sha256=HASH_B)
    assert len(DurableJournal(path).records()) == before
    assert account.option_positions == {"ACME-C100-NAKED": -1}

