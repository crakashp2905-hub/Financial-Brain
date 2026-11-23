"""FE-0042 deterministic no-trade reason codes for bad market data."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest

from financial_brain.forward_edge.data_gate import GateReason, evaluate_market_data
from financial_brain.forward_edge.data_permissions import DataPermission, DataUse, PermissionStatus

NOW = datetime(2026, 11, 15, 4, 0, tzinfo=timezone.utc)


def _good(**changes):
    args = dict(decision_time=NOW, quote_time=NOW - timedelta(milliseconds=10), bid=Decimal("99.99"),
                ask=Decimal("100.01"), max_age_ms=100, max_spread_bps=Decimal("5"), volume=1000,
                source_id="fixture", entitlement=DataPermission(
                    "fixture", DataUse.PAPER_SIMULATION, PermissionStatus.USE_ALLOWED,
                    NOW.date(), None, "tests/fixture-permission", "test"))
    args.update(changes)
    return evaluate_market_data(**args)


def test_good_observed_quote_passes_and_reason_order_is_stable() -> None:
    assert _good().decision == "TRADE"
    assert _good().data_status == "USABLE"
    bad = _good(quote_time=None, bid=None, ask=None, volume=0, is_circuit_limited=True,
                session_phase="AUCTION", auction_required=True, auction_data_present=False)
    assert bad.decision == "NO_TRADE"
    assert bad.reasons == tuple(sorted(bad.reasons, key=lambda r: r.value))
    assert bad.data_status == "DATA_UNUSABLE"
    assert {GateReason.QUOTE_MISSING, GateReason.ZERO_VOLUME, GateReason.CIRCUIT_LIMIT,
            GateReason.NOT_CONTINUOUS_SESSION, GateReason.AUCTION_DATA_MISSING} <= set(bad.reasons)


@pytest.mark.parametrize("changes,reason", [
    ({"quote_time": NOW - timedelta(seconds=1)}, GateReason.QUOTE_STALE),
    ({"ask": Decimal("101")}, GateReason.SPREAD_TOO_WIDE),
    ({"bid": Decimal("102")}, GateReason.BID_ASK_INVALID),
    ({"volume": None}, GateReason.ZERO_VOLUME),
    ({"quantity": 11, "freeze_limit": 10}, GateReason.FREEZE_LIMIT),
    ({"auction_required": True, "auction_data_present": False}, GateReason.AUCTION_DATA_MISSING),
    ({"session_phase": "PREOPEN"}, GateReason.NOT_CONTINUOUS_SESSION),
])
def test_each_bad_data_condition_abstains_with_reason(changes, reason) -> None:
    result = _good(**changes)
    assert result.decision == "NO_TRADE"
    assert reason in result.reasons


def test_future_and_naive_quote_timestamps_are_rejected() -> None:
    future = _good(quote_time=NOW + timedelta(milliseconds=1))
    assert GateReason.QUOTE_CLOCK_INVALID in future.reasons
    naive = _good(quote_time=datetime(2026, 11, 15, 3, 59))
    assert GateReason.QUOTE_CLOCK_INVALID in naive.reasons


def test_missing_expired_mismatched_or_wrong_use_rights_fail_closed() -> None:
    assert GateReason.RIGHTS_UNKNOWN in _good(entitlement=None).reasons
    expired = DataPermission("fixture", DataUse.PAPER_SIMULATION, PermissionStatus.USE_ALLOWED,
                             NOW.date() - timedelta(days=2),
                             NOW.date() - timedelta(days=1), "evidence", "reviewer")
    assert GateReason.RIGHTS_UNKNOWN in _good(entitlement=expired).reasons
    assert GateReason.RIGHTS_UNKNOWN in _good(source_id="other").reasons
    research_only = DataPermission("fixture", DataUse.RESEARCH, PermissionStatus.USE_ALLOWED,
                                   NOW.date(), None, "evidence", "reviewer")
    assert GateReason.RIGHTS_UNKNOWN in _good(entitlement=research_only).reasons
    denied = DataPermission("fixture", DataUse.PAPER_SIMULATION, PermissionStatus.DENIED,
                            None, None,
                            "evidence", "reviewer")
    assert GateReason.RIGHTS_DENIED in _good(entitlement=denied).reasons


def test_permission_registry_covers_every_known_data_source_and_use():
    root = Path(__file__).resolve().parents[1]
    sources = json.loads((root / "docs/edge-program/market-data-sources.v1.json").read_text())
    registry = json.loads((root / "docs/edge-program/data-permissions.v1.json").read_text())
    expected = {(row.get("runtime_source_id", row["id"]), use.value)
                for row in sources["sources"] for use in DataUse}
    actual = {(row["source_id"], row["use"]) for row in registry["permissions"]}
    assert actual == expected
    assert all(row["status"] == "UNKNOWN_RIGHTS" for row in registry["permissions"])
    for row in registry["permissions"]:
        permission = DataPermission.from_dict({
            "source_id": row["source_id"], "use": row["use"], "status": row["status"],
            "effective_from": row["effective_from"], "effective_through": row["effective_through"],
            "evidence_ref": row["evidence_ref"], "reviewer": row["reviewer"],
        })
        assert not permission.allows(source_id=row["source_id"], use=DataUse(row["use"]), on=NOW.date())
