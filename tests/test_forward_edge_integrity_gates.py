from __future__ import annotations

from datetime import date

from financial_brain.forward_edge.fixtures import fixture_pack
from financial_brain.forward_edge.pipeline import run_session


def _instrument_rules(*, status: str = "ACTIVE", expiry: str | None = None,
                      lot_size: int = 1) -> dict:
    return {
        "schema": "fb.instrument-rules.v1",
        "catalogue_id": "synthetic-regression-fixture",
        "version": "fixture-v1",
        "source_ref": "synthetic test data; not exchange rules",
        "instruments": {
            "INE009A01021": [{
                "effective_from": "1900-01-01",
                "tick_size": "0.05",
                "lot_size": lot_size,
                "freeze_quantity": 1000,
                "expiry": expiry,
                "status": status,
                "session_phases": ["CONTINUOUS"],
            }],
        },
    }


def _market_observation(**overrides: object) -> dict:
    observation = {
        "quote_time": "2027-01-04T03:45:59.900000+00:00",
        "bid": "1500.00",
        "ask": "1500.05",
        "max_age_ms": 1000,
        "max_spread_bps": "10",
        "volume": 1000,
        "freeze_limit": 1000,
        "session_phase": "CONTINUOUS",
        "auction_required": False,
        "auction_data_present": True,
    }
    observation.update(overrides)
    return observation


def _gated_fixture() -> dict:
    fixture = fixture_pack()["win"]
    fixture.update({
        "instrument_rules": _instrument_rules(),
        "instrument_rules_date": date(2027, 1, 4).isoformat(),
        "instrument_session_phase": "CONTINUOUS",
        "market_observation": _market_observation(),
    })
    return fixture


def test_integrity_gates_allow_clean_versioned_fixture_inputs() -> None:
    result = run_session(_gated_fixture())
    assert result["outcome"] == "WIN"
    assert "reject_codes" not in result
    assert result["physical_orders"] is False


def test_instrument_failure_blocks_execution_and_reports_stable_code() -> None:
    fixture = _gated_fixture()
    fixture["instrument_rules"] = _instrument_rules(status="SUSPENDED")
    result = run_session(fixture)
    assert result["outcome"] == "NO_TRADE"
    assert result["reject_codes"] == ["INSTRUMENT_NOT_TRADABLE_STATUS"]
    assert result["net_pnl"] == "0"
    assert "execution" not in [event["stage"] for event in result["trace"]]
    assert not {"orders", "fills", "ledger"} & set(result["segments"])


def test_multiple_market_data_failures_are_combined_deterministically() -> None:
    fixture = _gated_fixture()
    fixture["market_observation"] = _market_observation(
        quote_time="2027-01-04T03:44:00+00:00",
        bid="1500.00", ask="1510.00", max_spread_bps="10", volume=0,
        circuit_limited=True,
    )
    first = run_session(fixture)
    second = run_session(fixture)
    expected = ["DATA_CIRCUIT_LIMIT", "DATA_QUOTE_STALE", "DATA_SPREAD_TOO_WIDE", "DATA_ZERO_VOLUME"]
    assert first["outcome"] == "NO_TRADE"
    assert first["reject_codes"] == expected
    assert first["reject_codes"] == second["reject_codes"]
    assert first["signature"] == second["signature"]
    assert "execution" not in [event["stage"] for event in first["trace"]]


def test_malformed_present_market_gate_fails_closed() -> None:
    fixture = _gated_fixture()
    fixture["market_observation"] = {"max_age_ms": 1000}
    result = run_session(fixture)
    assert result["outcome"] == "NO_TRADE"
    assert result["reject_codes"] == ["DATA_GATE_INPUT_INVALID"]
