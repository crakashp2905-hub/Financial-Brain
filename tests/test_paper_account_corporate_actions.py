from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from financial_brain.kernel.oms import State
from financial_brain.kernel.paper_account import FeeSchedule, PaperAccount, PaperAccountError


SCHEDULE = FeeSchedule("corp-action-fees-v1", date(2026, 1, 1), Decimal("10"), Decimal("10"))
SOURCE_SHA = "a" * 64


def _account(path):
    return PaperAccount(path, account_id="corp-book", initial_cash=Decimal("1000"),
                        fee_schedule=SCHEDULE)


def _buy(account, *, symbol="NSE:INFY", quantity=2):
    account.submit_intent(client_id="buy-1", symbol=symbol, side="BUY", quantity=quantity,
                          limit=Decimal("100"), reserve=Decimal("205"))
    account.move("buy-1", State.SUBMITTED)
    account.move("buy-1", State.ACCEPTED)
    account.record_fill("buy-1", fill_id="fill-1", quantity=quantity,
                        price=Decimal("100"), filled_on=date(2026, 11, 11))


def test_split_preserves_total_cost_basis_invalidates_old_mark_and_replays(tmp_path):
    path = tmp_path / "split.jsonl"
    account = _account(path)
    _buy(account)
    account.record_mark("NSE:INFY", mark_id="pre-split", price=Decimal("100"),
                        observed_at=datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc))
    account.apply_stock_split("split-2-for-1", "NSE:INFY", ratio=Decimal("2"),
                              effective_on=date(2026, 11, 12), source_id="owner-action-table-v1",
                              source_sha256=SOURCE_SHA)

    assert account.position("NSE:INFY").quantity == 4
    assert account.position("NSE:INFY").average_cost == Decimal("50")
    with pytest.raises(PaperAccountError, match="current marks"):
        _ = account.equity
    account.record_mark("NSE:INFY", mark_id="post-split", price=Decimal("50"),
                        observed_at=datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc))
    reopened = _account(path)

    assert reopened.position("NSE:INFY").quantity == 4
    assert reopened.equity == Decimal("999.80")
    assert reopened.ledger.trial_balance_ok()


def test_dividend_posts_gross_income_withholding_and_net_cash_once(tmp_path):
    path = tmp_path / "dividend.jsonl"
    account = _account(path)
    _buy(account)
    before = path.read_bytes()
    with pytest.raises(PaperAccountError, match="eligible quantity"):
        account.apply_cash_dividend(
            "dividend-wrong-qty", "NSE:INFY", ex_date=date(2026, 11, 12),
            payment_date=date(2026, 11, 20), applied_on=date(2026, 11, 20), eligible_quantity=3,
            gross_per_share=Decimal("1"), withholding_rate=Decimal("0.10"),
            source_id="owner-dividend-table-v1", source_sha256=SOURCE_SHA,
        )
    assert path.read_bytes() == before
    account.apply_cash_dividend(
        "dividend-q1", "NSE:INFY", ex_date=date(2026, 11, 12), payment_date=date(2026, 11, 20),
        applied_on=date(2026, 11, 20),
        eligible_quantity=2, gross_per_share=Decimal("1"), withholding_rate=Decimal("0.10"),
        source_id="owner-dividend-table-v1", source_sha256=SOURCE_SHA,
    )
    account.apply_cash_dividend(
        "dividend-q1", "NSE:INFY", ex_date=date(2026, 11, 12), payment_date=date(2026, 11, 20),
        applied_on=date(2026, 11, 20),
        eligible_quantity=2, gross_per_share=Decimal("1"), withholding_rate=Decimal("0.10"),
        source_id="owner-dividend-table-v1", source_sha256=SOURCE_SHA,
    )

    reopened = _account(path)
    assert reopened.cash == Decimal("801.60")
    assert reopened.dividend_income == Decimal("2.00")
    assert reopened.taxes == Decimal("0.20")
    assert reopened.net_realized_pnl == Decimal("1.60")
    assert reopened.ledger.trial_balance_ok()


def test_suspension_blocks_intents_until_a_sourced_resumption(tmp_path):
    account = _account(tmp_path / "suspension.jsonl")
    account.set_instrument_suspended("suspend-1", "NSE:INFY", suspended=True,
                                     effective_on=date(2026, 11, 12), source_id="owner-status-v1",
                                     source_sha256=SOURCE_SHA)
    with pytest.raises(PaperAccountError, match="suspended or delisted"):
        account.submit_intent(client_id="blocked", symbol="NSE:INFY", side="BUY", quantity=1,
                              limit=Decimal("100"), reserve=Decimal("105"))
    account.set_instrument_suspended("resume-1", "NSE:INFY", suspended=False,
                                     effective_on=date(2026, 11, 13), source_id="owner-status-v1",
                                     source_sha256=SOURCE_SHA)
    account.submit_intent(client_id="allowed", symbol="NSE:INFY", side="BUY", quantity=1,
                          limit=Decimal("100"), reserve=Decimal("105"))
    assert account.oms.order("allowed").state is State.CREATED


def test_delisting_cash_recovery_realizes_loss_and_zero_recovery_is_supported(tmp_path):
    path = tmp_path / "delisting.jsonl"
    account = _account(path)
    _buy(account)
    account.settle_delisting("delist-1", "NSE:INFY", recovery_per_share=Decimal("80"),
                             effective_on=date(2026, 11, 12), source_id="owner-settlement-v1",
                             source_sha256=SOURCE_SHA)

    reopened = _account(path)
    assert reopened.position("NSE:INFY").quantity == 0
    assert reopened.cash == Decimal("959.80")
    assert reopened.realized_pnl == Decimal("-40.00")
    assert reopened.net_realized_pnl == Decimal("-40.20")
    assert reopened.ledger.trial_balance_ok()


def test_actions_without_source_hash_or_with_fractional_split_do_not_mutate_account(tmp_path):
    path = tmp_path / "refused.jsonl"
    account = _account(path)
    _buy(account)
    before = path.read_bytes()
    with pytest.raises(PaperAccountError, match="source SHA-256"):
        account.apply_stock_split("bad-source", "NSE:INFY", ratio=Decimal("2"),
                                  effective_on=date(2026, 11, 12), source_id="unverified", source_sha256="bad")
    with pytest.raises(PaperAccountError, match="fractional shares"):
        account.apply_stock_split("fractional", "NSE:INFY", ratio=Decimal("1.25"),
                                  effective_on=date(2026, 11, 12), source_id="owner-action-table-v1",
                                  source_sha256=SOURCE_SHA)
    assert path.read_bytes() == before
    assert account.position("NSE:INFY").quantity == 2
