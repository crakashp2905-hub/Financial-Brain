"""Idempotent import boundary from the option subledger into canonical paper accounting."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from financial_brain.paper.derivatives_subledger import DerivativesSubledger

from .canonical_ledger import CanonicalPortfolioLedger


def import_derivatives_package(subledger: DerivativesSubledger, canonical: CanonicalPortfolioLedger,
                               package_id: str) -> dict[str, int]:
    """Import a verified option package once, retaining source event identities and hashes.

    Unfilled packages generate no accounting events. Partial packages import only their
    observed legs; missing legs are never synthesized. Expiry imports use explicit paper
    settlement evidence and preserve that it is not an exchange-confirmed position.
    """
    events = subledger.to_portfolio_events(package_id)
    if not events:
        return {"received": 0, "added": 0, "already_present": 0}
    return canonical.import_events(events)


__all__ = ["import_derivatives_package"]
