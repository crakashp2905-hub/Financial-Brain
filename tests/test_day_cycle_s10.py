from __future__ import annotations

from datetime import date

from financial_brain.paper.day_cycle import DayCycle, Phase, RunStatus

MON, SAT = date(2026, 11, 16), date(2026, 11, 21)


def biz(d: date) -> bool:
    return d.weekday() < 5


def cycle(**h):
    calls: list[tuple[date, Phase]] = []
    handlers = {p: (lambda d, p=p: calls.append((d, p))) for p in Phase}
    handlers.update(h)
    return DayCycle(biz, handlers), calls


def test_business_day_runs_every_phase_in_order():
    c, calls = cycle()
    res = c.run(MON)
    assert all(s is RunStatus.DONE for _, s in res)
    assert [p for _, p in calls] == list(Phase)


def test_rerun_does_not_double_count():
    c, calls = cycle()
    c.run(MON); res = c.run(MON)
    assert all(s is RunStatus.SKIPPED_ALREADY_DONE for _, s in res)
    assert len(calls) == len(Phase)


def test_non_business_day_fabricates_nothing():
    c, calls = cycle()
    assert all(s is RunStatus.SKIPPED_NON_BUSINESS_DAY for _, s in c.run(SAT))
    assert calls == []


def test_failed_pre_open_halts_trading_phases_but_reconciles():
    def bad(d):
        raise RuntimeError("feed entitlement missing")
    c, calls = cycle(**{Phase.PRE_OPEN: bad})
    res = dict(c.run(MON))
    assert res[Phase.PRE_OPEN] is RunStatus.FAILED
    assert res[Phase.OPEN_EQUITY_ETF] is RunStatus.HALTED
    assert res[Phase.MF_BEFORE_CUTOFF] is RunStatus.HALTED
    assert res[Phase.DAILY_RECONCILE] is RunStatus.DONE


def test_fail_closed_mid_day_halts_remaining_trading():
    c, _ = cycle()
    c.fail_closed(MON, "drawdown limit")
    res = dict(c.run(MON))
    assert res[Phase.OPEN_OPTIONS] is RunStatus.HALTED
    assert c.log[2][3] == "drawdown limit"


def test_failed_phase_can_be_retried():
    state = {"n": 0}

    def flaky(d):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("timeout")
    c, _ = cycle(**{Phase.DAILY_RECONCILE: flaky})
    assert dict(c.run(MON))[Phase.DAILY_RECONCILE] is RunStatus.FAILED
    assert dict(c.run(MON))[Phase.DAILY_RECONCILE] is RunStatus.DONE
