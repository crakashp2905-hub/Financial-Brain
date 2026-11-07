"""Offline guard and boundary tests (CP-0011, CP-0149, CP-0218)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from financial_brain.patterns import offline

PKG = Path(__file__).resolve().parents[1] / "src" / "financial_brain" / "patterns"
FORBIDDEN_IMPORT_ROOTS = {"socket", "urllib", "http", "requests", "httpx", "kiteconnect", "financial_brain.execution", "financial_brain.paper"}


def test_default_mode_is_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(offline.ENV_VAR, raising=False)
    assert offline.current_mode() == "offline"


def test_guard_blocks_external_operations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(offline.ENV_VAR, "offline")
    with pytest.raises(offline.OfflineViolation, match="fetch quotes"):
        offline.guard_external("fetch quotes")


def test_unknown_mode_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(offline.ENV_VAR, "live")
    with pytest.raises(offline.OfflineViolation):
        offline.current_mode()


def test_no_pattern_module_imports_network_broker_or_oms() -> None:
    offenders: list[str] = []
    for path in sorted(PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if any(name == root or name.startswith(root + ".") for root in FORBIDDEN_IMPORT_ROOTS):
                    offenders.append(f"{path.name}: {name}")
    assert offenders == []
