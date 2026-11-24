"""Check that release colors keep infrastructure, research, and trading separate."""

from __future__ import annotations

import json
import sys
from pathlib import Path


SEMANTICS = Path("docs/edge-program/release-semantics.v1.json")
DOMAINS = {"infrastructure", "research", "trading"}
STATUSES = {"RED", "YELLOW", "GREEN"}


def validate(path: Path) -> list[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"cannot read release semantics: {exc}"]
    if not isinstance(payload, dict) or payload.get("schema") != "fb.edge_program.release_semantics.v1":
        return ["invalid release-semantics schema"]
    errors: list[str] = []
    if payload.get("status_order") != ["RED", "YELLOW", "GREEN"]:
        errors.append("status order must be RED, YELLOW, GREEN")
    domains = payload.get("domains")
    if not isinstance(domains, dict) or set(domains) != DOMAINS:
        return errors + ["release semantics must define infrastructure, research, and trading"]

    def check_subdomain(name: str, spec: object, *, require_non_inputs: bool) -> None:
        if not isinstance(spec, dict):
            errors.append(f"{name}: missing rule object")
            return
        expected = {"green_requires_all", "yellow_when_any", "red_when_any"}
        if require_non_inputs:
            expected.add("explicit_non_inputs_to_green")
        if set(spec) != expected:
            errors.append(f"{name}: missing or unexpected rule fields")
            return
        for field in expected:
            values = spec[field]
            if not isinstance(values, list) or not values or not all(
                    isinstance(value, str) and value.strip() for value in values):
                errors.append(f"{name}: {field} must be a nonempty list of explicit criteria")
        green = spec["green_requires_all"]
        if name == "research":
            required = {
                "independent_pre_entry_custody_verified_for_every_qualifying_session",
                "after_cost_net_alpha_lower_confidence_bound_exceeds_zero_and_policy_threshold",
                "dependence_robust_multiplicity_adjusted_inference_passes_preregistered_method",
            }
            if not isinstance(green, list) or not required.issubset(set(green)):
                errors.append("research GREEN must require independent custody, adjusted inference, and positive net-alpha bounds")
        if name == "trading.live_cash":
            required = {
                "paper_trading_green_for_the_same_strategy_account_and_risk_scope",
                "research_green_for_the_exact_frozen_strategy_and_promotion_cohort",
                "broker_and_exchange_compliance_verified_for_the_exact_route",
                "current_scoped_human_capital_and_strategy_approvals_verified",
            }
            if not isinstance(green, list) or not required.issubset(set(green)):
                errors.append("live-cash GREEN must require paper, research, compliance, and human-approval gates")
            forbidden = {"a_passing_test_suite", "infrastructure_green"}
            non_inputs = spec.get("explicit_non_inputs_to_green", [])
            if not forbidden.issubset(set(non_inputs)):
                errors.append("live-cash rules must explicitly reject tests and infrastructure GREEN as authorization")

    check_subdomain("infrastructure", domains.get("infrastructure"), require_non_inputs=False)
    check_subdomain("research", domains.get("research"), require_non_inputs=True)
    check_subdomain("trading.paper", domains.get("trading", {}).get("paper")
                     if isinstance(domains.get("trading"), dict) else None, require_non_inputs=False)
    check_subdomain("trading.live_cash", domains.get("trading", {}).get("live_cash")
                     if isinstance(domains.get("trading"), dict) else None, require_non_inputs=True)
    trading = domains.get("trading")
    if not isinstance(trading, dict) or set(trading) != {"paper", "live_cash"}:
        errors.append("trading must report separate paper and live_cash subdomains")
    aggregation = payload.get("aggregation")
    if not isinstance(aggregation, dict) or set(aggregation) != {
            "within_each_subdomain", "between_domains", "overall_release"}:
        errors.append("aggregation semantics must be explicit")
    elif any(not isinstance(value, str) or not value.strip() for value in aggregation.values()):
        errors.append("aggregation semantics must be nonempty text")
    return errors


if __name__ == "__main__":
    problems = validate(SEMANTICS)
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        raise SystemExit(1)
    print("Release semantics verified: infrastructure, research, paper, and live-cash remain independent")
