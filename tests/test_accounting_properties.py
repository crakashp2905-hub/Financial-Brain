from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from financial_brain.forward_edge.fixtures import fixture_pack
from financial_brain.forward_edge.pipeline import run_session
from financial_brain.kernel.oms import State
from financial_brain.kernel.india_options_expiry import DeliveryMarginEvidence
from financial_brain.kernel.options_paper import OptionContract, OptionsPaperAccount
from financial_brain.kernel.paper_account import FeeSchedule, PaperAccount, PaperAccountError


@pytest.mark.parametrize("entry,close", [(1500, 1490), (1500, 1510), (100, 125), (100, 75)])
def test_long_and_short_price_direction_has_opposite_gross_pnl(entry: int, close: int) -> None:
    long_case = fixture_pack()["win"]
    long_case.update(session_id=f"PROPERTY-LONG-{entry}-{close}", close_price=str(close))
    long_case["quotes"] = [{"offset_ms": 300, "bid": str(entry), "ask": str(Decimal(entry) + Decimal("0.05")),
                            "bid_qty": 100, "ask_qty": 100}]
    short_case = dict(long_case, session_id=f"PROPERTY-SHORT-{entry}-{close}", side="SELL")

    long_result = run_session(long_case)
    short_result = run_session(short_case)
    long_net, short_net = Decimal(long_result["net_pnl"]), Decimal(short_result["net_pnl"])

    assert long_result["ledger_balanced"] is True
    assert short_result["ledger_balanced"] is True
    long_gross = (Decimal(close) - Decimal(entry) - Decimal("0.05")) * 10
    short_gross = (Decimal(entry) - Decimal(close)) * 10
    assert (long_net > 0) is (long_gross > 0)
    assert (short_net > 0) is (short_gross > 0)
    assert long_net < long_gross and short_net < short_gross  # fees never improve the result
    assert long_result["outcome"] == ("WIN" if long_net > 0 else "LOSS" if long_net < 0 else "FLAT")
    assert short_result["outcome"] == ("WIN" if short_net > 0 else "LOSS" if short_net < 0 else "FLAT")


def _round_trip(tmp_path, bps: str) -> PaperAccount:
    schedule = FeeSchedule(f"fees-{bps}", date(2026, 1, 1), Decimal(bps), Decimal(bps))
    book = PaperAccount(tmp_path / f"fees-{bps}.jsonl", account_id=f"acct-{bps}",
                        initial_cash=Decimal("10000"), fee_schedule=schedule)
    book.submit_intent(client_id="buy", symbol="NSE:ABC", side="BUY", quantity=10,
                       limit=Decimal("100"), reserve=Decimal("1500"))
    book.move("buy", State.SUBMITTED)
    book.move("buy", State.ACCEPTED)
    book.record_fill("buy", fill_id="buy-fill", quantity=10, price=Decimal("100"),
                     filled_on=date(2026, 1, 2))
    book.submit_intent(client_id="sell", symbol="NSE:ABC", side="SELL", quantity=10,
                       limit=Decimal("110"), reserve=Decimal(0))
    book.move("sell", State.SUBMITTED)
    book.move("sell", State.ACCEPTED)
    book.record_fill("sell", fill_id="sell-fill", quantity=10, price=Decimal("110"),
                     filled_on=date(2026, 1, 2))
    return book


def test_higher_fees_never_improve_net_realized_pnl(tmp_path) -> None:
    books = [_round_trip(tmp_path, bps) for bps in ("0", "1", "5", "10", "25")]
    net = [book.net_realized_pnl for book in books]
    fees = [book.fees for book in books]
    assert fees == sorted(fees)
    assert net == sorted(net, reverse=True)
    assert all(book.ledger.trial_balance_ok() for book in books)


def test_replaying_same_fill_id_has_one_effect_but_mutated_payload_fails(tmp_path) -> None:
    book = PaperAccount(tmp_path / "idempotent.jsonl", account_id="identity",
                        initial_cash=Decimal("5000"))
    book.submit_intent(client_id="buy", symbol="NSE:ABC", side="BUY", quantity=5,
                       limit=Decimal("100"), reserve=Decimal("1000"))
    book.move("buy", State.SUBMITTED)
    book.move("buy", State.ACCEPTED)
    book.record_fill("buy", fill_id="stable-fill", quantity=5, price=Decimal("90"))
    sequence = book.journal.seq
    cash = book.cash
    book.record_fill("buy", fill_id="stable-fill", quantity=5, price=Decimal("90"))
    assert book.journal.seq == sequence and book.cash == cash
    with pytest.raises(PaperAccountError, match="fill id"):
        book.record_fill("buy", fill_id="stable-fill", quantity=4, price=Decimal("90"))
    assert book.journal.seq == sequence and book.cash == cash


def test_short_option_fill_and_expiry_pnl_follow_direction_and_margin(tmp_path) -> None:
    expiry = date(2026, 11, 27)
    contract = OptionContract(
        contract_id="ACME-C100", underlying="ACME", kind="CALL", strike=Decimal("100"),
        expiry=expiry, lot_size=10, settlement="CASH", short_margin_per_lot=Decimal("500"),
        source_id="fixture-contracts", source_sha256="a" * 64, underlying_kind="INDEX",
    )
    book = OptionsPaperAccount(tmp_path / "short-option.jsonl", account_id="short-options",
                               initial_cash=Decimal("5000"))
    book.trade("short-call", contract, quantity_lots=-1, premium=Decimal("5"),
               session_date=date(2026, 11, 13), source_sha256="b" * 64)
    assert book.option_positions == {"ACME-C100": -1}
    assert book.margin_required == Decimal("500")
    readiness = book.preflight_india_expiry(
        settlement_prices={"ACME": Decimal("110")}, session_date=expiry,
        settlement_source_sha256="c" * 64,
        delivery_margins={"ACME-C100": DeliveryMarginEvidence(
            "ACME-C100", Decimal("500"), "fixture-margin", "a" * 64)},
    )
    assert readiness.status == "READY"
    book.settle_expiry("short-call-expiry", settlement_prices={"ACME": Decimal("110")},
                       session_date=expiry, source_sha256="c" * 64, readiness=readiness)
    assert book.realized_pnl == Decimal("-50")
    assert book.option_positions == {}
    assert book.ledger.trial_balance_ok()
