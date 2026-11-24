"""Central physical-order policy shared by all Financial Brain execution paths.

This repository is configured for research and paper trading. Physical broker orders
are denied in code, independent of credentials, environment variables, UI flags,
approvals, rollout stages, or kill-switch state.
"""
from __future__ import annotations


PHYSICAL_ORDERS_ENABLED = False


class PhysicalOrderDenied(PermissionError):
    """Raised whenever a code path attempts physical broker execution."""


def deny_physical_order(action: str) -> None:
    """Fail closed for a broker-side write; there is no runtime enable switch."""
    raise PhysicalOrderDenied(f"{action} refused: physical broker orders are permanently disabled by build policy")
