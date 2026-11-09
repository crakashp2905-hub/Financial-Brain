"""Session preflight and safe configuration (FB-0540, FB-0552, FB-0651, FB-0529 subset).

* ``preflight`` runs named checks; the session may start only when every required check passes. A missing check is a
  failure, not a skip.
* ``load_config`` starts from safe defaults: mode PAPER, external orders disabled, sizing capped. A config that
  tries to set an unknown key or enable external orders is rejected.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from financial_brain.kernel.guards import Mode

SAFE_DEFAULTS: dict[str, object] = {
    "mode": Mode.PAPER.value,
    "external_orders": False,
    "max_order_notional": 50_000,
    "max_position_units": 1_000,
}
_ALLOWED_KEYS = frozenset(SAFE_DEFAULTS)


class ConfigError(ValueError):
    pass


@dataclass
class PreflightReport:
    passed: bool
    failures: list[str] = field(default_factory=list)
    results: dict[str, bool] = field(default_factory=dict)


def preflight(checks: Mapping[str, Callable[[], bool]], required: list[str]) -> PreflightReport:
    results: dict[str, bool] = {}
    failures: list[str] = []
    for name in required:
        check = checks.get(name)
        if check is None:
            results[name] = False
            failures.append(f"{name}: check not registered")
            continue
        try:
            ok = bool(check())
        except Exception as exc:  # a crashing check is a failed check
            ok = False
            failures.append(f"{name}: raised {type(exc).__name__}")
        else:
            if not ok:
                failures.append(f"{name}: failed")
        results[name] = ok
    return PreflightReport(passed=not failures, failures=failures, results=results)


def load_config(overrides: Mapping[str, object] | None = None) -> dict[str, object]:
    cfg = dict(SAFE_DEFAULTS)
    for key, value in (overrides or {}).items():
        if key not in _ALLOWED_KEYS:
            raise ConfigError(f"unknown config key {key!r}")
        cfg[key] = value
    if cfg["external_orders"] is not False:
        raise ConfigError("external_orders cannot be enabled by configuration")
    if cfg["mode"] not in {m.value for m in Mode}:
        raise ConfigError("unknown mode")
    if cfg["mode"] == Mode.LIVE_DISABLED.value and overrides and "mode" in overrides:
        raise ConfigError("LIVE_DISABLED is terminal and cannot be selected here")
    return cfg
