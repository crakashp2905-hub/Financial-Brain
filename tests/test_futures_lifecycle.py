from datetime import date
from decimal import Decimal

import pytest

from financial_brain.evaluation.futures_roll_schedule import (
    OISnapshot,
    RollScheduleError,
    build_roll_schedule,
)
from financial_brain.kernel.futures_paper import FuturesContract, FuturesPaperAccount, FuturesPaperError


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def _contract(expiry: date, *, multiplier: str, contract_id: str) -> FuturesContract:
    return FuturesContract(
        contract_id=contract_id,
        symbol="NIFTY",
        expiry=expiry,
        tick=Decimal("0.05"),
        lot_size=1,
        multiplier=Decimal(multiplier),
        initial_margin_per_lot=Decimal("1000"),
        maintenance_margin_per_lot=Decimal("700"),
        source_id="fixture-contract-master",
        source_sha256=HASH_A,
    )


def test_roll_variation_margin_funding_and_expiry_reconcile_across_restart(tmp_path):
    path = tmp_path / "futures-lifecycle.jsonl"
    account = FuturesPaperAccount(path, account_id="fno", initial_cash=Decimal("10000"))
    near = _contract(date(2026, 11, 27), multiplier="50", contract_id="NIFTY-NOV")
    far = _contract(date(2026, 12, 25), multiplier="25", contract_id="NIFTY-DEC")
    account.trade("open", near, quantity=2, price=Decimal("100"),
                  session_date=date(2026, 11, 20), source_sha256=HASH_B)
    account.settle("mark", settlement_price=Decimal("105"),
                   session_date=date(2026, 11, 23), source_sha256=HASH_B)
    cost = account.fund_overnight(
        "carry", annual_rate=Decimal("0.073"), mark_price=Decimal("105"), days=3,
        session_date=date(2026, 11, 23), source_sha256=HASH_C,
    )
    assert cost == Decimal("6.30")
    assert account.variation_pnl == Decimal("500.00")
    assert account.funding_pnl == Decimal("-6.30")
    assert account.net_pnl == Decimal("493.70")

    account.roll("roll", far, old_exit_price=Decimal("106"), new_entry_price=Decimal("107"),
                 session_date=date(2026, 11, 24), old_price_source_sha256=HASH_B,
                 new_price_source_sha256=HASH_C)
    account.expire("expiry", final_settlement_price=Decimal("110"),
                   session_date=far.expiry, source_sha256=HASH_C)
    assert account.variation_pnl == Decimal("750.00")
    assert account.net_pnl == Decimal("743.70")
    assert account.cash == Decimal("10743.70")
    recovered = FuturesPaperAccount(path, account_id="fno", initial_cash=Decimal("10000"))
    assert recovered.net_pnl == account.net_pnl
    assert recovered.cash == account.cash
    assert recovered.ledger.trial_balance_ok()


def test_overnight_funding_is_idempotent_and_cannot_double_charge_a_session(tmp_path):
    account = FuturesPaperAccount(tmp_path / "funding.jsonl", account_id="fno",
                                  initial_cash=Decimal("10000"))
    terms = _contract(date(2026, 11, 27), multiplier="50", contract_id="NIFTY-NOV")
    account.trade("open", terms, quantity=1, price=Decimal("100"),
                  session_date=date(2026, 11, 20), source_sha256=HASH_B)
    kwargs = dict(annual_rate=Decimal("0.073"), mark_price=Decimal("100"), days=1,
                  session_date=date(2026, 11, 23), source_sha256=HASH_C)
    first = account.fund_overnight("carry-1", **kwargs)
    assert account.fund_overnight("carry-1", **kwargs) == first
    with pytest.raises(FuturesPaperError, match="already posted"):
        account.fund_overnight("carry-2", **kwargs)


def _oi(day: int, front: int, next_: int) -> OISnapshot:
    return OISnapshot(
        session_date=date(2026, 11, day),
        front_contract_id="NIFTY-NOV",
        next_contract_id="NIFTY-DEC",
        front_open_interest=front,
        next_open_interest=next_,
        front_expiry=date(2026, 11, 27),
        next_expiry=date(2026, 12, 25),
        source_id="nse-fo-bhavcopy-fixture",
        source_sha256=HASH_B if day % 2 else HASH_C,
    )


def test_oi_roll_schedule_requires_confirmed_crossover_and_freezes_evidence():
    schedule = build_roll_schedule([
        _oi(20, 1000, 500),
        _oi(23, 400, 600),
        _oi(24, 300, 800),
    ])
    assert [row.decision for row in schedule.decisions] == ["HOLD", "HOLD", "ROLL"]
    assert schedule.decisions[-1].reason == "NEXT_CONTRACT_OI_CONFIRMED"
    assert len(schedule.policy_sha256) == len(schedule.evidence_sha256) == 64
    assert schedule == build_roll_schedule([_oi(20, 1000, 500), _oi(23, 400, 600), _oi(24, 300, 800)])


def test_roll_schedule_forces_before_expiry_and_rejects_bad_oi():
    forced = build_roll_schedule([_oi(26, 1000, 500)])
    assert forced.decisions[0].decision == "ROLL"
    assert forced.decisions[0].reason == "EXPIRY_BUFFER_REACHED"
    with pytest.raises(RollScheduleError, match="cannot be negative"):
        build_roll_schedule([_oi(20, -1, 2)])
