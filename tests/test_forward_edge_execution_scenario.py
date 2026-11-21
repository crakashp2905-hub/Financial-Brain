"""FE-0059 deterministic execution scenarios and resulting inventory."""
from decimal import Decimal

from financial_brain.forward_edge.execution_scenario import evaluate_passive_scenario, evaluate_scenario


def _run(**changes):
    args = dict(side="BUY", quantity=10, touch_price=Decimal("100"), limit_price=Decimal("101"),
                tick_size=Decimal("0.05"), visible_quantity=10, freeze_limit=20, slice_size=4,
                adverse_slippage_bps=Decimal("25"))
    args.update(changes)
    return evaluate_scenario(**args)


def test_adverse_selection_and_order_slices_update_inventory() -> None:
    fill = _run()
    assert fill.status == "FILLED"
    assert fill.average_price == Decimal("100.25")
    assert fill.child_quantities == (4, 4, 2)
    assert fill.inventory_delta == 10


def test_visible_depth_causes_partial_fill_and_no_liquidity_misses() -> None:
    partial = _run(visible_quantity=3)
    assert partial.status == "PARTIAL" and partial.filled_quantity == 3 and partial.inventory_delta == 3
    missed = _run(visible_quantity=0)
    assert missed.status == "MISSED" and missed.inventory_delta == 0


def test_adverse_price_limit_and_freeze_limit_reject_without_inventory() -> None:
    limit = _run(limit_price=Decimal("100.10"))
    assert limit.status == "MISSED" and limit.reject_reason == "ADVERSE_PRICE_BREACHES_LIMIT"
    freeze = _run(quantity=21, freeze_limit=20)
    assert freeze.status == "REJECTED" and freeze.inventory_delta == 0


def test_sell_slippage_is_adverse_and_inventory_direction_is_negative() -> None:
    sell = _run(side="SELL", touch_price=Decimal("100"), limit_price=Decimal("99"),
                adverse_slippage_bps=Decimal("25"))
    assert sell.average_price == Decimal("99.75")
    assert sell.inventory_delta == -10


def test_price_band_rejects_before_inventory_is_created() -> None:
    rejected = _run(price_band=(Decimal("90"), Decimal("100")))
    assert rejected.status == "REJECTED" and rejected.reject_reason == "PRICE_BAND"
    assert rejected.filled_quantity == 0 and rejected.inventory_delta == 0


def test_passive_limits_need_a_trade_through_and_apply_adverse_selection() -> None:
    missed = evaluate_passive_scenario(
        side="BUY", quantity=10, limit_price=Decimal("100"), tick_size=Decimal("0.05"),
        freeze_limit=20, slice_size=4, adverse_slippage_bps=Decimal("20"),
        trade_prints=((Decimal("100"), 10),),
    )
    filled = evaluate_passive_scenario(
        side="BUY", quantity=10, limit_price=Decimal("100"), tick_size=Decimal("0.05"),
        freeze_limit=20, slice_size=4, adverse_slippage_bps=Decimal("20"),
        trade_prints=((Decimal("99.95"), 6), (Decimal("99.90"), 4),),
    )
    assert missed.status == "MISSED" and missed.reject_reason == "NO_TRADE_THROUGH_LIMIT"
    assert filled.status == "FILLED" and filled.average_price == Decimal("100.00")
    assert filled.child_quantities == (4, 4, 2)
