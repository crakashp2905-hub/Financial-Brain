from __future__ import annotations

import pytest

from financial_brain.kernel.contracts import (
    AccountId,
    ErrorCategory,
    EvidenceTier,
    KernelError,
    PortfolioId,
    require_account,
    retry_allowed,
    satisfies,
)


def test_portfolio_id_cannot_satisfy_an_account_scope_even_with_same_string() -> None:
    assert require_account(AccountId("A1")) == AccountId("A1")
    with pytest.raises(TypeError):
        require_account(PortfolioId("A1"))


def test_empty_scope_ids_are_refused() -> None:
    with pytest.raises(ValueError):
        AccountId("  ")
    with pytest.raises(ValueError):
        PortfolioId("")


def test_only_reversible_errors_are_retryable() -> None:
    assert retry_allowed(KernelError("RATE_LIMIT", ErrorCategory.REVERSIBLE)) is True
    assert retry_allowed(KernelError("CONTRACT_EXPIRED", ErrorCategory.TERMINAL, "no retry")) is False


def test_kernel_error_message_carries_code() -> None:
    err = KernelError("STALE_QUOTE", ErrorCategory.REVERSIBLE, "quote 9s old")
    assert err.code == "STALE_QUOTE"
    assert "STALE_QUOTE" in str(err)


def test_unsigned_evidence_never_satisfies_a_signed_requirement() -> None:
    assert satisfies(EvidenceTier.UNSIGNED_REPLAY, EvidenceTier.SIGNED_REPLAY) is False
    assert satisfies(EvidenceTier.UNSIGNED_DRAFT, EvidenceTier.UNSIGNED_REPLAY) is False
    assert satisfies(EvidenceTier.SIGNED_REPLAY, EvidenceTier.SIGNED_REPLAY) is True
    assert satisfies(EvidenceTier.SIGNED_REPLAY, EvidenceTier.UNSIGNED_REPLAY) is True
