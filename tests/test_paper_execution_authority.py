from __future__ import annotations

import pytest

from financial_brain.kernel.paper_authority import (
    PaperAuthorityError,
    PaperExecutionAuthorityRegistry,
    configured_paper_authority_path,
)


def test_claim_is_idempotent_but_prevents_a_second_engine_or_account(tmp_path):
    registry = PaperExecutionAuthorityRegistry(tmp_path / "authority.sqlite3")
    first = registry.claim(strategy_id="momentum", exchange_date="2026-11-12",
                           engine_id="forward_paper", account_id="book-1")
    assert registry.claim(strategy_id="momentum", exchange_date="2026-11-12",
                          engine_id="forward_paper", account_id="book-1") == first
    with pytest.raises(PaperAuthorityError, match="already claimed"):
        registry.claim(strategy_id="momentum", exchange_date="2026-11-12",
                       engine_id="legacy_intraday", account_id="book-1")
    with pytest.raises(PaperAuthorityError, match="already claimed"):
        registry.claim(strategy_id="momentum", exchange_date="2026-11-12",
                       engine_id="forward_paper", account_id="book-2")


def test_claim_many_is_atomic_when_one_strategy_conflicts(tmp_path):
    registry = PaperExecutionAuthorityRegistry(tmp_path / "authority.sqlite3")
    registry.claim(strategy_id="occupied", exchange_date="2026-11-12",
                   engine_id="forward_paper", account_id="book-1")
    with pytest.raises(PaperAuthorityError, match="already claimed"):
        registry.claim_many([
            {"strategy_id": "free", "exchange_date": "2026-11-12",
             "engine_id": "legacy_intraday", "account_id": "book-1"},
            {"strategy_id": "occupied", "exchange_date": "2026-11-12",
             "engine_id": "legacy_intraday", "account_id": "book-1"},
        ])
    # A different engine can still claim `free`, proving the failed transaction left no row.
    registry.claim(strategy_id="free", exchange_date="2026-11-12",
                   engine_id="forward_paper", account_id="book-2")


@pytest.mark.parametrize("claim", [
    {"strategy_id": "", "exchange_date": "2026-11-12", "engine_id": "engine", "account_id": "book"},
    {"strategy_id": "s", "exchange_date": "2026-02-30", "engine_id": "engine", "account_id": "book"},
])
def test_invalid_authority_claims_fail_closed(tmp_path, claim):
    registry = PaperExecutionAuthorityRegistry(tmp_path / "authority.sqlite3")
    with pytest.raises(PaperAuthorityError):
        registry.claim(**claim)


def test_deployment_override_unifies_authority_roots(monkeypatch, tmp_path):
    configured = tmp_path / "shared" / "authority.sqlite3"
    monkeypatch.setenv("FINANCIAL_BRAIN_PAPER_AUTHORITY_DB", str(configured))
    assert configured_paper_authority_path(tmp_path / "one") == configured.resolve()
    assert configured_paper_authority_path(tmp_path / "two") == configured.resolve()
