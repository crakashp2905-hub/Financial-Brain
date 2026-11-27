"""Fixed-seed accounting properties across cash books and corporate actions."""
from __future__ import annotations

import random
from datetime import datetime, timezone
from decimal import Decimal

from financial_brain.kernel.batch20_accounting import dividend_postings
from financial_brain.kernel.lineage import Holding, adjust_for_split, reconciles
from financial_brain.kernel.oms import State
from financial_brain.kernel.paper_account import PaperAccount


def _fill_in_chunks(book: PaperAccount, client_id: str, total: int, price: Decimal, prefix: str) -> None:
    remaining = total
    rng = random.Random(sum(ord(c) for c in prefix))
    index = 0
    while remaining:
        quantity = rng.randint(1, remaining)
        book.record_fill(client_id, fill_id=f"{prefix}-{index}", quantity=quantity, price=price)
        remaining -= quantity
        index += 1


def test_seeded_cash_equity_partial_fill_and_exit_books_conserve_cash_units_and_net_pnl(tmp_path):
    for seed in range(20):
        rng = random.Random(seed)
        initial_cash = Decimal("1000000")
        quantity = rng.randint(12, 80)
        buy_price = Decimal(rng.randint(70, 150))
        sell_price = buy_price + Decimal(rng.randint(1, 40))
        sold = rng.randint(1, quantity - 1)
        buy_fee = Decimal(rng.randint(1, 25)) / Decimal("100")
        sell_fee = Decimal(rng.randint(1, 25)) / Decimal("100")
        book = PaperAccount(tmp_path / f"account-{seed}.jsonl", account_id=f"acct-{seed}",
                            initial_cash=initial_cash)
        book.submit_intent(client_id="buy", symbol="NSE:TEST", side="BUY", quantity=quantity,
                           limit=buy_price, reserve=buy_price * quantity)
        book.move("buy", State.SUBMITTED)
        book.move("buy", State.ACCEPTED)
        _fill_in_chunks(book, "buy", quantity, buy_price, f"buy-{seed}")
        book.record_fee("buy-fee", buy_fee)

        book.submit_intent(client_id="sell", symbol="NSE:TEST", side="SELL", quantity=sold,
                           limit=sell_price, reserve=Decimal(0))
        book.move("sell", State.SUBMITTED)
        book.move("sell", State.ACCEPTED)
        _fill_in_chunks(book, "sell", sold, sell_price, f"sell-{seed}")
        book.record_fee("sell-fee", sell_fee)
        book.record_mark("NSE:TEST", mark_id="close", price=sell_price,
                         observed_at=datetime(2026, 11, 20, 10, tzinfo=timezone.utc))

        expected_cash = initial_cash - buy_price * quantity + sell_price * sold - buy_fee - sell_fee
        expected_units = quantity - sold
        expected_gross = (sell_price - buy_price) * sold
        expected_marked_net = (sell_price - buy_price) * quantity - buy_fee - sell_fee
        assert book.cash == expected_cash
        assert book.position("NSE:TEST").quantity == expected_units
        assert book.realized_pnl == expected_gross
        assert book.fees == buy_fee + sell_fee
        assert book.net_realized_pnl == expected_gross - buy_fee - sell_fee
        assert book.net_pnl == expected_marked_net
        assert book.ledger.balance(f"cash:acct-{seed}", "INR") == book.cash
        assert book.ledger.trial_balance_ok()


def test_seeded_split_and_dividend_entries_conserve_notional_and_gross_cash():
    rng = random.Random(20261127)
    for _ in range(100):
        units = Decimal(rng.randint(1, 100_000))
        basis = Decimal(rng.randint(1, 100_000)) / Decimal("100")
        ratio = Decimal(rng.randint(1, 20)) / Decimal(rng.randint(1, 20))
        before = Holding(units, basis)
        after = adjust_for_split(before, ratio)
        notional_error = abs(after.notional - before.notional)
        assert notional_error < Decimal("0.000001")
        assert reconciles(before, after, before.notional, tolerance=Decimal("0.000001"))

        dividend_per_unit = Decimal(rng.randint(1, 1_000)) / Decimal("100")
        withholding_rate = Decimal(rng.randint(0, 30)) / Decimal("100")
        postings = dividend_postings(units, dividend_per_unit, withholding_rate)
        gross = units * dividend_per_unit
        assert postings["dividend_income"] == gross
        assert postings["cash"] + postings["withholding"] == gross
