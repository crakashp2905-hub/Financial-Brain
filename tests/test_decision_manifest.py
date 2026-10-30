from copy import deepcopy

import pytest

from financial_brain.evidence.decision_manifest import ManifestError, canonical_bytes, digest


H = "a" * 64


def sample():
    return {
        "schema": "fb.decision.v1", "day": "2026-10-31", "decision_id": "d-1",
        "author": "paper-research", "strategy_sha256": H, "model_sha256": H,
        "data_sha256": H, "universe_sha256": H, "picks_sha256": H,
        "calendar_sha256": H, "cost_model_sha256": H, "risk_limits_sha256": H,
        "decision_at_utc": "2026-10-31T10:30:00Z",
        "entry_earliest_at_utc": "2026-11-02T03:45:00Z",
        "exit_scheduled_at_utc": "2026-12-01T10:00:00Z",
        "holding_sessions": 20, "benchmark_id": "NIFTY500-TRI",
        "capital_inr": "100000",
        "predicted_return_fraction": "0.02", "predicted_cost_fraction": "0.002",
        "forecast": {"p10_fraction": "-0.05", "p50_fraction": "0.01",
                     "p90_fraction": "0.08", "probability_exceeds_cost": "0.55"},
        "no_trade_reasons": [],
        "selections": [
            {"lineage": "L2", "symbol": "B", "weight_fraction": "0.25", "quantity": 10},
            {"lineage": "L1", "symbol": "A", "weight_fraction": "0.25", "quantity": 10},
        ],
        "exclusions": [{"lineage": "L3", "reason": "stale quote"}],
        "sources": [{"source_id": "kite-minute", "sha256": H,
                     "published_at_utc": "2026-10-31T10:29:00Z",
                     "received_at_utc": "2026-10-31T10:29:01Z"}],
    }


def test_canonical_snapshot_is_stable_without_mutating_input():
    original = sample()
    before = deepcopy(original)
    encoded = canonical_bytes(original)
    assert original == before
    swapped = sample()
    swapped["selections"].reverse()
    assert canonical_bytes(swapped) == encoded
    assert digest(swapped) == digest(original)


@pytest.mark.parametrize("change", [
    lambda x: x.update(predicted_cost_fraction="20"),
    lambda x: x.update(predicted_cost_fraction=float("nan")),
    lambda x: x.update(entry_earliest_at_utc="2026-10-31T10:00:00Z"),
    lambda x: x["sources"][0].update(received_at_utc="2026-10-31T10:31:00Z"),
    lambda x: x["sources"][0].update(received_at_utc="2026-10-31T10:28:00Z"),
    lambda x: x["selections"][1].update(lineage="L2"),
    lambda x: x["selections"][0].update(weight_fraction="0.9"),
    lambda x: x.update(schema="fb.decision.v2"),
    lambda x: x["forecast"].update(p10_fraction="0.1"),
    lambda x: x.update(capital_inr="-1"),
    lambda x: x.update(broker_order_id="not allowed"),
])
def test_invalid_or_future_information_fails_closed(change):
    value = sample()
    change(value)
    with pytest.raises(ManifestError):
        canonical_bytes(value)


def test_revising_universe_or_picks_changes_digest():
    value = sample()
    revised = sample()
    revised["universe_sha256"] = "b" * 64
    assert digest(revised) != digest(value)
    revised = sample()
    revised["picks_sha256"] = "b" * 64
    assert digest(revised) != digest(value)
