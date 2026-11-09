from __future__ import annotations

import json
from decimal import Decimal

from financial_brain.kernel.events import EventKernel
from financial_brain.kernel.pilot import Bar, cash_equals_net_pnl, run_pilot, synthetic_tape


def _find_tape(drift: float, want: str, seeds: range = range(1, 60)) -> list[Bar]:
    """Scan deterministic seeds for a tape whose trade outcome matches ``want`` (WIN, LOSS or NO_TRADE)."""
    for seed in seeds:
        bars = synthetic_tape(seed, drift=drift)
        if run_pilot(bars).outcome == want:
            return bars
    raise AssertionError(f"no seed produced {want}")


def test_pilot_win_full_chain_balanced_and_traced() -> None:
    bars = _find_tape(0.0009, "WIN")
    res = run_pilot(bars)
    stages = [r["stage"] for r in res.trace]
    assert stages == ["opening_range", "signal", "gate", "risk", "fill", "exit", "ledger"]
    assert res.ledger.trial_balance_ok()
    assert cash_equals_net_pnl(res)
    assert res.pnl > 0


def test_pilot_loss_is_reported_honestly() -> None:
    bars = _find_tape(-0.0009, "LOSS")
    res = run_pilot(bars)
    assert res.outcome == "LOSS" and res.pnl < 0
    assert res.ledger.trial_balance_ok() and cash_equals_net_pnl(res)


def test_flat_tape_without_breakout_is_no_trade_not_a_fill() -> None:
    bars = _find_tape(0.0, "NO_TRADE")
    res = run_pilot(bars)
    assert res.submissions == 0 and len(res.ledger) == 0
    assert res.trace[-1]["stage"] == "signal"


def test_too_few_bars_abstains() -> None:
    res = run_pilot(synthetic_tape(1, drift=0.0)[:5])
    assert res.trace == [{"stage": "gate", "decision": "NO_TRADE", "missing": ["insufficient bars"]}]


def test_trace_is_reproducible_and_jsonl_parses() -> None:
    bars = _find_tape(0.0009, "WIN")
    a, b = run_pilot(bars).jsonl(), run_pilot(bars).jsonl()
    assert a == b
    for line in a.strip().splitlines():
        json.loads(line)


def test_resume_does_not_resubmit_the_entry() -> None:
    bars = _find_tape(0.0009, "WIN")
    kernel = EventKernel()
    first = run_pilot(bars, kernel=kernel)
    second = run_pilot(bars, kernel=kernel)  # simulated restart reusing the dispatched-key ledger
    assert first.submissions == 1
    assert second.submissions == 0
    assert second.outcome == first.outcome


def test_synthetic_tape_is_labelled_and_deterministic() -> None:
    a = synthetic_tape(7, drift=0.0)
    b = synthetic_tape(7, drift=0.0)
    assert a == b and len(a) == 375
    assert all(isinstance(x.c, float) and x.c > 0 for x in a)
    assert isinstance(run_pilot(a).pnl, Decimal)
