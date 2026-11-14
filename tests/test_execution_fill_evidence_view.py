from __future__ import annotations

from financial_brain.forward_edge import cockpit


SHA = "a" * 64


def test_simulated_quote_fill_is_labeled_and_shows_every_execution_input():
    receipt = {
        "receipt_id": "sim-1", "fidelity": "SIMULATED_QUOTE",
        "fills": [{
            "fill_id": "fill-1", "execution_nature": "SIMULATED", "quantity": 2, "price": "101.05",
            "execution_model_id": "quote-v1", "execution_model_sha256": SHA,
            "fee_schedule_sha256": "b" * 64, "latency_model_sha256": "c" * 64,
            "data_snapshot_sha256": "d" * 64,
            "quote_observation": {
                "instrument": "NSE:INFY", "exchange": "NSE", "source": "kite",
                "event_utc": "2026-11-12T04:00:00+00:00",
                "received_utc": "2026-11-12T04:00:00.001000+00:00",
                "available_utc": "2026-11-12T04:00:00.002000+00:00",
                "bid_price": "100.90", "ask_price": "101.02", "bid_qty": 20, "ask_qty": 3,
                "quote_event_sha256": "e" * 64,
            },
        }],
    }
    html = cockpit.execution_fill_table([receipt])
    assert "SIMULATED ONLY — NOT AN EXCHANGE FILL" in html
    for field in ("quote-v1", "execution_model_sha256", "fee_schedule_sha256", "latency_model_sha256",
                  "data_snapshot_sha256", "100.90", "101.02", "kite", "available_utc"):
        assert field in html


def test_observed_label_needs_provider_identity_and_read_only_reconciliation():
    unverified = {"receipt_id": "obs-bad", "fidelity": "OBSERVED_BROKER",
                  "fills": [{"fill_id": "x", "quantity": 1, "price": "100"}]}
    assert "OBSERVED LABEL UNVERIFIED" in cockpit.execution_fill_table([unverified])

    observed = {"receipt_id": "obs-1", "fidelity": "OBSERVED_BROKER", "fills": [{
        "fill_id": "local-1", "execution_nature": "OBSERVED", "provider_fill_id": "broker-fill-1",
        "source": "kite-readonly-tradebook", "exchange": "NSE",
        "observed_at_utc": "2026-11-12T04:01:00+00:00", "source_sha256": "f" * 64,
        "broker_session_id": "readonly-session-1", "reconciled": True,
        "quantity": 1, "price": "100",
    }]}
    assert "OBSERVED READ-ONLY FILL" in cockpit.execution_fill_table([observed])


def test_no_fill_receipt_does_not_invent_an_execution():
    html = cockpit.execution_fill_table([{"receipt_id": "no-fill", "fidelity": "SIMULATED_QUOTE", "fills": []}])
    assert "NO FILL" in html and "No execution is implied" in html
