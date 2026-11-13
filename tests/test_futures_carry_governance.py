from __future__ import annotations

from datetime import date
from decimal import Decimal as D

from financial_brain.forward_edge.futures_carry import CarryInputs, candidate_specs, evaluate_carry
from financial_brain.forward_edge.registry import Registry, Status, order_intent_allowed


def _inputs(**over):
    args = dict(
        as_of=date(2027, 1, 4), expiry=date(2027, 1, 28), spot=D("1000"), future=D("1010"),
        transaction_cost_per_unit=D("1"), funding_cost_per_unit=D("3"),
        margin_financing_cost_per_unit=D("1"), dividend_per_unit=D("0"),
        contract_identity_verified=True, corporate_actions_resolved=True, data_rights_verified=True,
        eligible_universe_verified=True, position_limits_known=True,
    )
    args.update(over)
    return CarryInputs(**args)


def test_positive_gross_basis_is_reduced_by_all_costs_and_never_paper_eligible():
    result = evaluate_carry(_inputs())
    assert result["basis_per_unit"] == "10"
    assert result["net_carry_per_unit"] == "5"
    assert result["verdict"] == "NET_CARRY_POSITIVE_RESEARCH_ONLY"
    assert result["paper_eligible"] is False


def test_incomplete_economics_and_governance_stay_shadow_only():
    missing = evaluate_carry(_inputs(funding_cost_per_unit=None))
    assert missing["verdict"] == "INSUFFICIENT_ECONOMIC_INPUTS"
    assert missing["missing"] == ["funding_cost"]
    unresolved = evaluate_carry(_inputs(data_rights_verified=False))
    assert unresolved["verdict"] == "SHADOW_ONLY_UNRESOLVED_GOVERNANCE"
    assert unresolved["missing_governance"] == ["data_rights_verified"]
    assert unresolved["paper_eligible"] is False


def test_futures_candidates_are_explicitly_shadow_and_cannot_pass_registry_intent_gate():
    registry = Registry()
    specs = candidate_specs()
    assert len(specs) == 3
    for spec in specs:
        registry.register(spec)
        assert spec.status is Status.SHADOW_ONLY
        assert not registry.can_place_paper_intents(spec.strategy_id)
        assert not order_intent_allowed(spec, registry)
