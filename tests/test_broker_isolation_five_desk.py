"""Hostile isolation tests: no five-desk paper path can reach a broker (FB-0006, FB-0007, FB-1318)."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from financial_brain.execution.oms import kite_broker
from financial_brain.forward_edge import modes

ROOT = Path(__file__).resolve().parents[1] / "src" / "financial_brain"
PAPER_MODULES = [
    "instruments/pit.py", "instruments/etf_reference.py", "instruments/mf_scheme.py",
    "signals/contracts.py", "signals/exit_triggers.py", "signals/exit_plans.py",
    "signals/admissibility.py", "signals/index_options.py",
    "execution/exchange_paper.py", "execution/mf_orders.py", "execution/mf_plans.py",
    "execution/option_book.py", "portfolio/multi_asset_ledger.py", "portfolio/position_ledger.py",
    "portfolio/tax_lots.py", "risk/capital_allocator.py", "paper/router.py", "paper/day_cycle.py",
    "paper/control_room.py", "paper/five_desk_cli.py",
]
FORBIDDEN = ("kite_broker", "urllib", "requests", "http", "socket", "kiteconnect")


@pytest.mark.parametrize("rel", PAPER_MODULES)
def test_paper_modules_import_no_broker_or_network(rel):
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    bad = [n for n in names if any(f in n.split(".") or n.endswith(f) for f in FORBIDDEN)]
    assert not bad, f"{rel} imports {bad}"


def test_physical_orders_policy_is_off():
    assert modes.PHYSICAL_ORDERS_ENABLED is False


def test_broker_locks_closed_without_owner_env(monkeypatch, tmp_path):
    monkeypatch.delenv("FB_LIVE_ORDERS", raising=False)
    closed = kite_broker.locks("FORWARD_VERIFIED", stage_path=tmp_path / "stage.json",
                               ks_path=tmp_path / "ks.json")
    assert any("FB_LIVE_ORDERS" in c for c in closed)


def test_accidental_env_flip_still_blocked_by_stage(monkeypatch, tmp_path):
    monkeypatch.setenv("FB_LIVE_ORDERS", "1")
    closed = kite_broker.locks("FORWARD_VERIFIED", stage_path=tmp_path / "stage.json",
                               ks_path=tmp_path / "ks.json")
    assert closed, "an env flip alone must not open the broker"


def test_broker_refuses_unlisted_endpoint():
    b = kite_broker.KiteBroker("X", opener=lambda *a, **k: None)
    with pytest.raises(kite_broker.BrokerError):
        b._request("POST", "/margins/equity")
