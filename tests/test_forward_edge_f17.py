"""F17 - data-integrity gates wired into the session pipeline (FE-0039, FE-0042, FE-0044). Offline fixtures."""
from __future__ import annotations

import pytest

from financial_brain.forward_edge import cli, pipeline
from financial_brain.forward_edge.fixtures import fixture_pack

GATED = {
    "halted_instrument": ["INSTRUMENT_NOT_TRADABLE_STATUS"],
    "expired_contract": ["INSTRUMENT_EXPIRED"],
    "wide_spread_gate": ["DATA_WIDE_SPREAD"],
    "stale_and_frozen_gate": ["DATA_FREEZE", "DATA_STALE"],
}


@pytest.mark.parametrize("name, codes", sorted(GATED.items()))
def test_a_failed_gate_rejects_the_trade_with_deterministic_reason_codes(name: str, codes: list[str]) -> None:
    r = pipeline.run_session(fixture_pack()[name])
    assert r["outcome"] == "NO_TRADE" and r["reject_codes"] == codes
    assert r["reason"] == "GATE:" + "+".join(codes)
    assert r["net_pnl"] == "0" and r["ledger_balanced"] is True


@pytest.mark.parametrize("name", sorted(GATED))
def test_a_gated_session_never_reaches_execution_or_the_ledger(name: str) -> None:
    r = pipeline.run_session(fixture_pack()[name])
    stages = [t["stage"] for t in r["trace"]]
    assert "gate" in stages and "execution" not in stages and "ledger" not in stages
    assert "fills" not in r["segments"] and "orders" not in r["segments"]


def test_reason_codes_are_identical_on_every_run() -> None:
    a = pipeline.run_session(fixture_pack()["stale_and_frozen_gate"])
    b = pipeline.run_session(fixture_pack()["stale_and_frozen_gate"])
    assert a["reject_codes"] == b["reject_codes"] and a["signature"] == b["signature"]


def test_clean_market_and_instrument_data_do_not_block_a_normal_trade() -> None:
    fx = fixture_pack()["win"]
    fx["instrument_master"] = {"isin": "INE009A01021", "symbol": "INFY", "lot": 1, "tick": "0.05", "status": "ACTIVE",
                               "rights_ok": True}
    fx["market"] = {"age_s": 1, "spread_bps": 5, "volume": 1000, "circuit_hit": False, "frozen": False, "auction_seen": True}
    r = pipeline.run_session(fx)
    assert r["outcome"] == "WIN" and "reject_codes" not in r


def test_instrument_and_data_failures_are_reported_together() -> None:
    fx = fixture_pack()["win"]
    fx["instrument_master"] = {"isin": "INE009A01021", "symbol": "INFY", "lot": 0, "tick": "0.05", "status": "DELISTED",
                               "rights_ok": False}
    fx["market"] = {"age_s": 1, "spread_bps": None, "volume": 0, "circuit_hit": True, "frozen": False, "auction_seen": False}
    r = pipeline.run_session(fx)
    assert r["reject_codes"] == sorted(["INSTRUMENT_NOT_TRADABLE_STATUS", "INSTRUMENT_BAD_LOT", "INSTRUMENT_NO_DATA_RIGHTS",
                                        "DATA_WIDE_SPREAD", "DATA_ZERO_VOLUME", "DATA_CIRCUIT", "DATA_MISSING_AUCTION"])


def test_the_report_carries_coverage_by_session_name_and_notional() -> None:
    out, code = cli.cmd_report("2027-01-04")
    cov = out["coverage"]
    assert set(cov) == {"sessions_pct", "names_pct", "intent_notional_pct"} and code == cli.EXIT["INSUFFICIENT"]
    assert 0 < cov["sessions_pct"] < 100 and cov["names_pct"] == 100.0       # gated and late sessions lower session coverage
    assert out["scheduled_sessions"] == 12
