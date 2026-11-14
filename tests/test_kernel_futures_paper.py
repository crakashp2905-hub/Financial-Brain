from datetime import date
from decimal import Decimal

import pytest

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.kernel.futures_paper import FuturesContract, FuturesPaperAccount, FuturesPaperError


HASH_A = "a" * 64
HASH_B = "b" * 64


def contract(expiry: date = date(2027, 1, 28), *, lot_size: int = 1) -> FuturesContract:
    return FuturesContract(
        contract_id=f"NIFTY-{expiry.isoformat()}",
        symbol="NIFTY",
        expiry=expiry,
        tick=Decimal("0.05"),
        lot_size=lot_size,
        multiplier=Decimal("1"),
        initial_margin_per_lot=Decimal("100"),
        maintenance_margin_per_lot=Decimal("75"),
        source_id="fixture-contract-master",
        source_sha256=HASH_A,
    )


def test_daily_variation_margin_recovery_and_idempotent_events(tmp_path):
    path = tmp_path / "futures.jsonl"
    account = FuturesPaperAccount(path, account_id="acct", initial_cash=Decimal("10000"))
    terms = contract()
    account.trade("open", terms, quantity=2, price=Decimal("100"),
                  session_date=date(2026, 11, 13), source_sha256=HASH_B)
    account.settle("settle-1", settlement_price=Decimal("110"),
                   session_date=date(2026, 11, 13), source_sha256=HASH_B)
    assert account.cash == Decimal("10020.00")
    assert account.variation_pnl == Decimal("20.00")
    assert account.position == 2
    assert account.initial_margin == Decimal("200")
    assert account.available_cash == Decimal("9820.00")
    account.settle("settle-1", settlement_price=Decimal("110"),
                   session_date=date(2026, 11, 13), source_sha256=HASH_B)

    account.settle("settle-2", settlement_price=Decimal("100"),
                   session_date=date(2026, 11, 16), source_sha256=HASH_B)
    assert account.cash == Decimal("10000.00")
    assert account.position == 2
    assert account.variation_pnl == Decimal("0.00")

    # Restart folds exactly the same cash and position.
    recovered = FuturesPaperAccount(path, account_id="acct", initial_cash=Decimal("10000"))
    assert (recovered.cash, recovered.position, recovered.variation_pnl) == (
        account.cash, account.position, account.variation_pnl
    )
    assert recovered.ledger.trial_balance_ok()

    with pytest.raises(FuturesPaperError, match="different futures request"):
        recovered.settle("settle-1", settlement_price=Decimal("109"),
                         session_date=date(2026, 11, 16), source_sha256=HASH_B)


def test_trade_margin_gate_roll_lot_compatibility_and_expiry(tmp_path):
    path = tmp_path / "futures.jsonl"
    account = FuturesPaperAccount(path, account_id="acct", initial_cash=Decimal("1000"))
    old = contract(date(2026, 11, 27), lot_size=2)
    account.trade("open", old, quantity=2, price=Decimal("100"),
                  session_date=date(2026, 11, 13), source_sha256=HASH_B)

    incompatible = contract(date(2026, 12, 25), lot_size=3)
    with pytest.raises(FuturesPaperError, match="whole lot"):
        account.roll("bad-roll", incompatible, old_exit_price=Decimal("100"),
                     new_entry_price=Decimal("101"), session_date=date(2026, 11, 16),
                     old_price_source_sha256=HASH_B, new_price_source_sha256=HASH_B)
    assert account.position == 2
    assert len(DurableJournal(path).records()) == 2

    new = contract(date(2026, 12, 25), lot_size=1)
    account.roll("roll", new, old_exit_price=Decimal("105"), new_entry_price=Decimal("106"),
                 session_date=date(2026, 11, 16),
                 old_price_source_sha256=HASH_B, new_price_source_sha256=HASH_B)
    assert account.position == 2
    assert account.active_contract == new
    assert account.cash == Decimal("1010.00")

    account.expire("expire", final_settlement_price=Decimal("103"),
                   session_date=new.expiry, source_sha256=HASH_B)
    assert account.position == 0
    assert account.active_contract is None
    assert account.cash == Decimal("1004.00")
    recovered = FuturesPaperAccount(path, account_id="acct", initial_cash=Decimal("1000"))
    assert recovered.cash == account.cash
    assert recovered.position == 0


def test_margin_rejects_trade_without_appending_and_blocks_stale_order(tmp_path):
    path = tmp_path / "futures.jsonl"
    account = FuturesPaperAccount(path, account_id="acct", initial_cash=Decimal("100"))
    terms = contract()
    with pytest.raises(FuturesPaperError, match="insufficient initial margin"):
        account.trade("too-large", terms, quantity=2, price=Decimal("100"),
                      session_date=date(2026, 11, 13), source_sha256=HASH_B)
    assert account.position == 0
    assert len(DurableJournal(path).records()) == 1

    account.trade("open", terms, quantity=1, price=Decimal("100"),
                  session_date=date(2026, 11, 13), source_sha256=HASH_B)
    with pytest.raises(FuturesPaperError, match="effective-date order"):
        account.settle("stale", settlement_price=Decimal("101"),
                       session_date=date(2026, 11, 12), source_sha256=HASH_B)
    assert len(DurableJournal(path).records()) == 2

