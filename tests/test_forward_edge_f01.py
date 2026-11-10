"""F01 - transactional correctness (FE-0011 .. FE-0019). Deterministic, offline, no broker.

Evidence map (see docs/forward-edge/TRACKER.md for status and the open gaps):
  FE-0011 side-effect outbox marks DISPATCHED only after success          -> test_failed_action_is_unknown_not_acknowledged
  FE-0013 UNKNOWN blocks replay until reconciled                          -> test_unknown_is_never_replayed_without_reconciliation
  FE-0014 invalid fills leave all state bit-identical                     -> test_rejected_fills_leave_state_bit_identical
  FE-0015 buying power falls with spent notional                          -> test_full_buy_reduces_buying_power
  FE-0016 10,000 randomized transitions reconcile                         -> test_ten_thousand_random_transitions_reconcile
  FE-0017 repeated pilot run creates no phantom fill                      -> test_pilot_repeat_run_posts_no_phantom_fill
  FE-0019 duplicate postbacks / stale ACK / cancel race                   -> test_duplicate_postback_is_one_economic_fill, ...
"""
from __future__ import annotations

import random
from decimal import Decimal

import pytest

from financial_brain.kernel.events import EventKernel, UnknownOutcome
from financial_brain.kernel.oms import Oms, OmsError, State
from financial_brain.kernel.pilot import run_pilot, synthetic_tape

ACCOUNT = "paper-inr"


# ---------- FE-0011 / FE-0013: side-effect outbox ----------

def _boom() -> None:
    raise TimeoutError("lost ack")


def _crash() -> None:
    raise RuntimeError("crash mid-send")


def test_failed_action_is_unknown_not_acknowledged() -> None:
    k = EventKernel()
    calls: list[str] = []

    def flaky() -> None:
        calls.append("attempt")
        raise TimeoutError("broker ACK lost")

    with pytest.raises(UnknownOutcome):
        k.side_effect("order-1", flaky)
    assert "order-1" not in k.side_effects          # never recorded as sent
    assert "order-1" in k.unknown_keys              # recoverable, not forgotten
    assert "order-1" not in set(k.checkpoint()["dispatched"])
    assert "order-1" in k.checkpoint()["unknown"]


def test_unknown_is_never_replayed_without_reconciliation() -> None:
    k = EventKernel()
    calls: list[str] = []

    def flaky() -> None:
        calls.append("attempt")
        raise TimeoutError("lost ack")

    with pytest.raises(UnknownOutcome):
        k.side_effect("order-1", flaky)
    with pytest.raises(UnknownOutcome, match="reconcile"):
        k.side_effect("order-1", lambda: calls.append("retry"))  # must not run
    assert calls == ["attempt"]

    # reconciliation proves the order never reached the venue: a retry is then legitimate
    k.resolve_unknown("order-1", took_effect=False, reconciled_by="operator:ops-1")
    assert k.side_effect("order-1", lambda: calls.append("retry")) is True
    assert calls == ["attempt", "retry"]


def test_reconciliation_that_found_the_order_closes_it_without_resend() -> None:
    k = EventKernel()
    with pytest.raises(UnknownOutcome):
        k.side_effect("order-2", lambda: _boom())
    k.resolve_unknown("order-2", took_effect=True, reconciled_by="operator:ops-1")
    assert k.side_effect("order-2", lambda: pytest.fail("must not resend")) is False
    assert k.reconciliations == [("order-2", True, "operator:ops-1")]


def test_resolution_requires_a_named_reconciler_and_an_unknown_key() -> None:
    k = EventKernel()
    with pytest.raises(UnknownOutcome, match="not in UNKNOWN"):
        k.resolve_unknown("never-seen", took_effect=False, reconciled_by="ops")
    with pytest.raises(UnknownOutcome):
        k.side_effect("order-3", lambda: _boom())
        k.resolve_unknown("order-3", took_effect=False, reconciled_by="  ")


def test_unknown_state_survives_checkpoint_and_resume() -> None:
    k = EventKernel()
    with pytest.raises(UnknownOutcome):
        k.side_effect("order-9", lambda: _crash())
    cp = k.checkpoint()
    restored = EventKernel()
    restored.resume(cp, [])
    with pytest.raises(UnknownOutcome):
        restored.side_effect("order-9", lambda: pytest.fail("replayed an UNKNOWN key"))


# ---------- OMS helpers ----------

def _oms(cash: str = "100000") -> Oms:
    return Oms({ACCOUNT: Decimal(cash)})


def _buy(oms: Oms, cid: str = "c1", qty: int = 100, reserve: str = "50000", limit: str | None = "500") -> None:
    oms.submit_intent(client_id=cid, account=ACCOUNT, side="BUY", quantity=qty,
                      limit=Decimal(limit) if limit else None, reserve=Decimal(reserve))


def _accepted(oms: Oms, cid: str = "c1") -> None:
    oms.move(cid, State.SUBMITTED)
    oms.move(cid, State.ACCEPTED)


def _snapshot(oms: Oms, account: str = ACCOUNT, ids: list[str] | None = None) -> tuple:
    ids = ids if ids is not None else []
    orders = tuple(
        (cid, o.state.value, o.filled, o.reserved, tuple(o.fills))
        for cid in sorted(ids) for o in [oms.order(cid)]
    )
    return (oms.cash(account), oms.reserved(account), orders)


# ---------- FE-0014: validate before mutate ----------

@pytest.mark.parametrize(
    "setup, qty, price",
    [
        ("accepted", 0, "499"),           # zero quantity
        ("accepted", -5, "499"),          # negative quantity
        ("accepted", 101, "499"),         # beyond remaining
        ("accepted", 10, "0"),            # non-positive price
        ("accepted", 10, "501"),          # through the buy limit
        ("created", 10, "499"),           # fill before the order was ever sent
        ("filled", 1, "499"),             # fill on a finished order
        ("accepted", 10, "NaN"),          # non-finite price
    ],
)
def test_rejected_fills_leave_state_bit_identical(setup: str, qty: int, price: str) -> None:
    oms = _oms()
    _buy(oms)
    if setup in ("accepted", "filled"):
        _accepted(oms)
    if setup == "filled":
        oms.record_fill("c1", 100, Decimal("499"))
    before = _snapshot(oms, ids=["c1"])
    with pytest.raises(OmsError):
        oms.record_fill("c1", qty, Decimal(price))
    assert _snapshot(oms, ids=["c1"]) == before


def test_fill_that_would_overspend_is_refused_without_mutation() -> None:
    oms = _oms("1000")
    oms.submit_intent(client_id="c1", account=ACCOUNT, side="BUY", quantity=10, limit=None,
                      reserve=Decimal("1000"))
    _accepted(oms)
    before = _snapshot(oms, ids=["c1"])
    with pytest.raises(OmsError):
        oms.record_fill("c1", 10, Decimal("150"))   # 1500 spend against a 1000 reservation
    assert _snapshot(oms, ids=["c1"]) == before


def test_cannot_mark_filled_with_open_quantity() -> None:
    oms = _oms()
    _buy(oms)
    _accepted(oms)
    oms.record_fill("c1", 10, Decimal("499"))
    with pytest.raises(OmsError, match="open"):
        oms.move("c1", State.FILLED)


# ---------- FE-0015 / FE-0016: one cash ledger, partial reservations ----------

def test_full_buy_reduces_buying_power() -> None:
    oms = _oms()
    _buy(oms)
    _accepted(oms)
    oms.record_fill("c1", 100, Decimal("500"))
    assert oms.cash(ACCOUNT) == Decimal("50000")
    assert oms.available(ACCOUNT) == Decimal("50000")  # previously reported 100000: the spend was not counted


def test_partial_fill_releases_only_its_proportion_of_reservation() -> None:
    oms = _oms()
    _buy(oms)
    _accepted(oms)
    oms.record_fill("c1", 40, Decimal("500"))
    assert oms.order("c1").reserved == Decimal("30000")   # 60% of the 50000 reservation still held
    assert oms.cash(ACCOUNT) == Decimal("80000")
    assert oms.available(ACCOUNT) == Decimal("50000")


def _random_snapshot_check(oms: Oms, ids: list[str], start: Decimal) -> None:
    total_reserved = Decimal(0)
    expected_cash = start
    for cid in ids:
        o = oms.order(cid)
        total_reserved += o.reserved
        assert 0 <= o.filled <= o.quantity
        assert o.reserved >= 0
        assert o.reserved <= o.initial_reserve
        if o.state in (State.FILLED, State.CANCELED, State.REJECTED, State.EXPIRED):
            assert o.reserved == 0
        if o.state is State.FILLED:
            assert o.filled == o.quantity
        for qty, price in o.fills:
            notional = qty * price
            expected_cash += -notional if o.side == "BUY" else notional
    assert oms.reserved(ACCOUNT) == total_reserved
    assert oms.cash(ACCOUNT) == expected_cash
    assert oms.available(ACCOUNT) == expected_cash - total_reserved
    assert oms.available(ACCOUNT) >= 0


def test_ten_thousand_random_transitions_reconcile() -> None:
    rng = random.Random(20261110)
    start = Decimal("1000000")
    oms = Oms({ACCOUNT: start})
    ids: list[str] = []
    accepted_ops = refused_ops = 0
    for _ in range(10_000):
        before = _snapshot(oms, ids=ids) if ids else None
        roll = rng.random()
        try:
            if roll < 0.05 or not ids:
                cid = f"o{len(ids)}"
                side = rng.choice(["BUY", "SELL"])
                limit = Decimal(rng.randint(90, 110)) if rng.random() < 0.5 else None
                reserve = Decimal(rng.randint(0, 20_000)) if side == "BUY" else Decimal(0)
                oms.submit_intent(client_id=cid, account=ACCOUNT, side=side, quantity=rng.randint(1, 200),
                                  limit=limit, reserve=reserve)
                ids.append(cid)
            elif roll < 0.45:
                oms.move(rng.choice(ids), rng.choice(list(State)))
            else:
                cid = rng.choice(ids)
                o = oms.order(cid)
                qty = rng.randint(-1, o.remaining + 1)
                price = Decimal(rng.randint(-1, 120)) + Decimal("0.25") if rng.random() < 0.97 else Decimal("NaN")
                pid = f"p{rng.randint(0, 3)}" if rng.random() < 0.3 else None
                oms.record_fill(cid, qty, price, provider_fill_id=pid)
            accepted_ops += 1
        except OmsError:
            refused_ops += 1
            # a refused transition or fill must not have changed anything
            assert _snapshot(oms, ids=ids) == before
        _random_snapshot_check(oms, ids, start)
    assert accepted_ops > 1_000 and refused_ops > 1_000  # the run exercised both paths


# ---------- FE-0019: duplicate postbacks, stale ACKs, cancel races ----------

def test_duplicate_postback_is_one_economic_fill() -> None:
    oms = _oms()
    _buy(oms)
    _accepted(oms)
    oms.record_fill("c1", 30, Decimal("499"), provider_fill_id="exch-77")
    oms.record_fill("c1", 30, Decimal("499"), provider_fill_id="exch-77")  # replayed postback
    assert oms.order("c1").filled == 30
    assert oms.order("c1").fills == [(30, Decimal("499"))]
    assert oms.cash(ACCOUNT) == Decimal("85030")


def test_same_provider_id_with_different_content_is_refused() -> None:
    oms = _oms()
    _buy(oms)
    _accepted(oms)
    oms.record_fill("c1", 30, Decimal("499"), provider_fill_id="exch-77")
    with pytest.raises(OmsError, match="reused"):
        oms.record_fill("c1", 31, Decimal("499"), provider_fill_id="exch-77")
    assert oms.order("c1").filled == 30


def test_stale_acknowledgement_after_fill_is_refused() -> None:
    oms = _oms()
    _buy(oms)
    _accepted(oms)
    oms.record_fill("c1", 100, Decimal("500"))
    with pytest.raises(OmsError, match="illegal transition"):
        oms.move("c1", State.ACCEPTED)
    assert oms.order("c1").state is State.FILLED


def test_fill_racing_a_cancel_is_one_fill_and_the_cancel_resolves_after() -> None:
    oms = _oms()
    _buy(oms)
    _accepted(oms)
    oms.move("c1", State.CANCEL_PENDING)
    oms.record_fill("c1", 60, Decimal("499"), provider_fill_id="exch-5")
    oms.move("c1", State.CANCELED)
    assert oms.order("c1").filled == 60
    assert oms.available(ACCOUNT) == Decimal("100000") - Decimal("29940")


# ---------- FE-0017: pilot phantom fills ----------

def _tape_with(outcome: str, drift: float) -> list:
    for seed in range(1, 60):
        bars = synthetic_tape(seed, drift=drift)
        if run_pilot(bars).outcome == outcome:
            return bars
    raise AssertionError(outcome)


def test_pilot_win_and_loss_reconcile_oms_cash_to_ledger() -> None:
    for outcome, drift in (("WIN", 0.0009), ("LOSS", -0.0009)):
        res = run_pilot(_tape_with(outcome, drift))
        assert res.outcome == outcome
        assert res.trace[-1]["oms_reconciled"] is True


def test_pilot_repeat_run_posts_no_phantom_fill() -> None:
    bars = _tape_with("WIN", 0.0009)
    kernel = EventKernel()
    first = run_pilot(bars, kernel=kernel)
    again = run_pilot(bars, kernel=kernel)
    assert first.outcome == "WIN"
    assert again.outcome == "DUPLICATE_SUPPRESSED"
    assert again.submissions == 0 and len(again.ledger) == 0
    assert again.trace[-1]["decision"] == "DUPLICATE_SUPPRESSED"


def test_pilot_failed_dispatch_is_unknown_and_posts_nothing() -> None:
    bars = _tape_with("WIN", 0.0009)
    kernel = EventKernel()
    # Pre-poison the key the way a lost acknowledgement would: an UNKNOWN outcome for this entry.
    entry_key = f"entry:ABC:{bars[0].ts.date().isoformat()}"
    with pytest.raises(UnknownOutcome):
        kernel.side_effect(entry_key, lambda: _boom())
    res = run_pilot(bars, kernel=kernel)
    assert res.outcome == "UNKNOWN_OUTCOME"
    assert res.submissions == 0 and len(res.ledger) == 0
