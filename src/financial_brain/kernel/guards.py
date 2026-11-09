"""Operating-mode guard, prerequisite gate and honest display helpers (FB-0038, FB-0227, FB-0651, FB-0711).

* Modes are explicit. Only ``PAPER``, ``REPLAY`` and ``RESEARCH`` are reachable from code in this repository. There
  is no path from this module to an external order: ``LIVE_DISABLED`` is the only live-related mode and it is
  terminal.
* An environment variable never changes the mode. Arming external trading would need a separate, owner-signed
  approval, which this codebase does not generate; so ``external_orders_armed`` is always False.
* A trade decision requires every declared prerequisite to be explicitly satisfied. Unknown (``None``) and false
  both abstain, so missing data never silently becomes an optimistic assumption.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


class Mode(str, Enum):
    RESEARCH = "RESEARCH"
    REPLAY = "REPLAY"
    PAPER = "PAPER"
    LIVE_DISABLED = "LIVE_DISABLED"


class ModeError(PermissionError):
    pass


def require_non_live(mode: Mode) -> Mode:
    if mode is Mode.LIVE_DISABLED:
        raise ModeError("LIVE_DISABLED: this mode cannot place or route external orders")
    return mode


def external_orders_armed(env: Mapping[str, str] | None = None, approval: object | None = None) -> bool:
    """Always False. Environment variables and in-process approvals cannot arm external trading in this codebase."""
    del env, approval
    return False


@dataclass(frozen=True)
class GateResult:
    decision: str
    missing: tuple[str, ...]

    @property
    def tradable(self) -> bool:
        return self.decision == "TRADE"


def prerequisite_gate(prerequisites: Mapping[str, Any]) -> GateResult:
    """TRADE only when at least one prerequisite is declared and every one is explicitly True."""
    if not prerequisites:
        return GateResult("NO_TRADE", ("no prerequisites declared",))
    missing = tuple(sorted(name for name, ok in prerequisites.items() if ok is not True))
    if missing:
        return GateResult("NO_TRADE", missing)
    return GateResult("TRADE", ())


def display_quote(value: float | None, *, decimals: int = 2) -> str:
    """An absent quote is never rendered as zero (FB-0038)."""
    if value is None:
        return "NO QUOTE"
    return f"{value:.{decimals}f}"
