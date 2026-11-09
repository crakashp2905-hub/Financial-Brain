"""Cross-module contracts: distinct scopes, error categories, evidence tiers (FB-0049, FB-0052, FB-0056).

* ``AccountId`` and ``PortfolioId`` are distinct types. Passing one where the other is required is refused, so a
  portfolio id can never grant account-level permission.
* ``KernelError`` carries a category: ``REVERSIBLE`` errors may be retried, ``TERMINAL`` errors must not be.
* ``EvidenceTier`` makes signed and unsigned evidence explicit. Only ``SIGNED_REPLAY`` satisfies a signed requirement.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class AccountId:
    value: str

    def __post_init__(self) -> None:
        if not self.value or not self.value.strip():
            raise ValueError("AccountId must be non-empty")


@dataclass(frozen=True)
class PortfolioId:
    value: str

    def __post_init__(self) -> None:
        if not self.value or not self.value.strip():
            raise ValueError("PortfolioId must be non-empty")


def require_account(scope: object) -> AccountId:
    """Account-level permission requires an AccountId. A PortfolioId is refused even with the same string."""
    if not isinstance(scope, AccountId):
        raise TypeError(f"account scope required, got {type(scope).__name__}")
    return scope


class ErrorCategory(Enum):
    REVERSIBLE = "reversible"  # safe to retry after a delay or a state refresh
    TERMINAL = "terminal"  # must not be retried; a human or a new intent is required


class KernelError(Exception):
    def __init__(self, code: str, category: ErrorCategory, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.category = category


def retry_allowed(error: KernelError) -> bool:
    return error.category is ErrorCategory.REVERSIBLE


class EvidenceTier(Enum):
    UNSIGNED_DRAFT = 1  # produced by an unreviewed run; never a basis for promotion
    UNSIGNED_REPLAY = 2  # deterministic replay with no signature; reproducible but not attested
    SIGNED_REPLAY = 3  # deterministic replay with a signature over its artifact hash


def satisfies(tier: EvidenceTier, required: EvidenceTier) -> bool:
    """A tier satisfies a requirement only at or above it; signed requirements are not met by unsigned evidence."""
    return tier.value >= required.value
