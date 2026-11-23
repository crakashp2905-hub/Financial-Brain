from __future__ import annotations

import ast
from pathlib import Path

import financial_brain


def test_http_api_has_no_physical_broker_order_route_or_adapter_import() -> None:
    api_root = Path(financial_brain.__file__).parent / "api"
    forbidden_names = {"KiteBroker", "KiteBrokerClient", "place_order", "cancel_order"}
    forbidden_modules = {"financial_brain.execution.oms.kite_broker",
                         "financial_brain.execution.oms.kite_broker.KiteBroker"}
    physical_routes: list[str] = []

    for path in api_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in forbidden_modules:
                raise AssertionError(f"API imports a physical broker adapter: {path}:{node.lineno}")
            if isinstance(node, ast.Import):
                if any(alias.name in forbidden_modules for alias in node.names):
                    raise AssertionError(f"API imports a physical broker adapter: {path}:{node.lineno}")
            if isinstance(node, ast.Name) and node.id in forbidden_names:
                raise AssertionError(f"API references a physical order operation: {path}:{node.lineno}")
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                if decorator.func.attr.lower() not in {"post", "put", "patch", "delete"}:
                    continue
                if decorator.args and isinstance(decorator.args[0], ast.Constant):
                    route = str(decorator.args[0].value)
                    if route.startswith(("/orders", "/broker")):
                        physical_routes.append(f"{path}:{node.lineno} {route}")

    assert physical_routes == []
