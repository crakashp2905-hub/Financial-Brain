from dataclasses import replace
from decimal import Decimal

import pytest

from financial_brain.forward_edge.cohort_separation import (
    CohortBook,
    CohortPair,
    CohortSeparationError,
)


def _pair():
    return CohortPair(
        shadow=CohortBook("momentum-shadow-1", "SHADOW", "a" * 64,
                          "shadow-signal-log", "shadow-ledger", Decimal("0")),
        live_paper=CohortBook("momentum-paper-1", "LIVE_PAPER", "b" * 64,
                              "paper-fill-simulator", "paper-ledger", Decimal("100000")),
    )


def test_shadow_and_live_paper_books_have_distinct_identity_policy_assets_and_capital():
    pair = _pair()
    assert len(pair.digest()) == 64
    assert pair.authorize_evidence_claim(source_mode="SHADOW", claim="PROSPECTIVE_EXECUTION") is False
    assert pair.authorize_evidence_claim(source_mode="SHADOW", claim="OBSERVED_FILL") is False
    assert pair.authorize_evidence_claim(source_mode="LIVE_PAPER", claim="PROSPECTIVE_EXECUTION") is True


@pytest.mark.parametrize("field", ["cohort_id", "policy_sha256", "execution_asset_id", "ledger_id",
                                    "capital_inr"])
def test_shadow_and_live_paper_may_not_alias_any_book_resource(field):
    pair = _pair()
    shadow = pair.shadow
    paper = pair.live_paper
    changed = getattr(shadow, field)
    if field == "capital_inr":
        changed = paper.capital_inr
    elif field == "policy_sha256":
        changed = paper.policy_sha256
    else:
        changed = getattr(paper, field)
    shadow = replace(shadow, **{field: changed})
    with pytest.raises(CohortSeparationError, match="distinct"):
        CohortPair(shadow, paper)


def test_pair_modes_are_fixed_and_live_paper_requires_positive_capital():
    pair = _pair()
    with pytest.raises(CohortSeparationError, match="modes"):
        CohortPair(pair.live_paper, pair.shadow)
    with pytest.raises(CohortSeparationError, match="positive"):
        CohortPair(pair.shadow, CohortBook("paper-2", "LIVE_PAPER", "b" * 64,
                                           "fill-sim-2", "ledger-2", Decimal(0)))
