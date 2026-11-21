"""Centralised offline guard for the chart-pattern package (CP-0011, CP-0375).

``PATTERN_DEV_MODE`` selects the mode. The default is ``offline``; any other
value is rejected so a typo cannot silently enable external access. In offline
mode every call to :func:`guard_external` raises :class:`OfflineViolation`.
"""

from __future__ import annotations

import os

ENV_VAR = "PATTERN_DEV_MODE"
OFFLINE = "offline"
_ALLOWED_MODES = frozenset({OFFLINE})


class OfflineViolation(RuntimeError):
    """Raised when pattern code attempts a network, broker or credential operation."""


def current_mode() -> str:
    """Return the active mode; unset means offline, unknown values fail closed."""
    raw = os.environ.get(ENV_VAR, OFFLINE).strip().lower()
    if raw not in _ALLOWED_MODES:
        raise OfflineViolation(f"{ENV_VAR}={raw!r} is not an allowed mode")
    return raw


def guard_external(operation: str) -> None:
    """Fail closed for any external operation while the package is offline."""
    if current_mode() == OFFLINE:
        raise OfflineViolation(f"external operation blocked in offline mode: {operation}")
