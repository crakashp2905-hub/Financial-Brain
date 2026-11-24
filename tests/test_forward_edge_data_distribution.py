from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.data_distribution import (
    DistributionError,
    PriceWindow,
    ProviderWindow,
    compare_windows,
)

START = datetime(2026, 11, 1, tzinfo=timezone.utc)
END = START + timedelta(days=7)
HASH_A = "a" * 64
HASH_B = "b" * 64


def _price(*, volatility: float = 0.01, net_return: float = 0.0) -> PriceWindow:
    return PriceWindow(START, END, 100, volatility, net_return)


def _provider(*, coverage: float = 0.99, stale: float = 0.01,
              out_of_order: float = 0.0, missing: float = 0.0,
              mix: tuple[tuple[str, float], ...] = (("KITE", 1.0),),
              schema: str = HASH_A) -> ProviderWindow:
    return ProviderWindow(START, END, 100, coverage, stale, out_of_order, missing, mix, schema)


def test_price_regime_shift_does_not_imply_provider_distribution_shift():
    result = compare_windows(_price(), _price(volatility=0.03), _provider(), _provider())
    assert result.status == "PRICE_REGIME_SHIFT"
    assert result.price_regime_shift
    assert not result.provider_distribution_shift
    assert result.provider_reasons == ()


def test_provider_shift_does_not_imply_price_regime_shift():
    changed = _provider(coverage=0.75, stale=0.2,
                       mix=(("KITE", 0.5), ("EXCHANGE", 0.5)), schema=HASH_B)
    result = compare_windows(_price(), _price(), _provider(), changed)
    assert result.status == "PROVIDER_DISTRIBUTION_SHIFT"
    assert not result.price_regime_shift
    assert result.provider_distribution_shift
    assert {"QUOTE_COVERAGE_SHIFT", "STALE_QUOTE_RATE_SHIFT", "SOURCE_MIX_SHIFT",
            "SCHEMA_FINGERPRINT_CHANGED"} <= set(result.provider_reasons)
    assert result.source_mix_total_variation == pytest.approx(0.5)


def test_independent_changes_are_reported_as_both_not_collapsed():
    result = compare_windows(_price(), _price(net_return=0.08), _provider(),
                             _provider(out_of_order=0.2))
    assert result.status == "BOTH_SHIFTED"
    assert result.price_regime_shift and result.provider_distribution_shift
    assert result.price_reasons == ("RETURN_DISTRIBUTION_SHIFT",)
    assert result.provider_reasons == ("OUT_OF_ORDER_RATE_SHIFT",)


def test_insufficient_support_abstains_without_claiming_stability():
    sparse = PriceWindow(START, END, 3, 0.1, 0.5)
    sparse_feed = ProviderWindow(START, END, 3, 0.1, 0.5, 0.5, 0.5, (("KITE", 1.0),), HASH_A)
    result = compare_windows(sparse, sparse, sparse_feed, sparse_feed)
    assert result.status == "INSUFFICIENT_SUPPORT"
    assert not result.price_regime_shift and not result.provider_distribution_shift


@pytest.mark.parametrize(
    "mutate",
    [
        lambda: ProviderWindow(START, END, 100, 1.1, 0, 0, 0, (("KITE", 1.0),), HASH_A),
        lambda: ProviderWindow(START, END, 100, 1, 0, 0, 0, (("KITE", 0.9),), HASH_A),
        lambda: ProviderWindow(START, END, 100, 1, 0, 0, 0, (("KITE", 1.0),), "bad"),
        lambda: PriceWindow(START, END, 100, float("nan"), 0),
    ],
)
def test_malformed_or_unbounded_window_metrics_fail_closed(mutate):
    with pytest.raises(DistributionError):
        mutate()


def test_unmatched_windows_are_not_compared():
    later = END + timedelta(days=7)
    with pytest.raises(DistributionError, match="same observation interval"):
        compare_windows(_price(), PriceWindow(END, later, 100, 0.01, 0),
                        _provider(), _provider())
