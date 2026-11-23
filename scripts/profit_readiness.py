"""Generate a deterministic, evidence-linked profitability readiness snapshot.

The report deliberately contains no progress percentages. Use --check in CI to
reject hand edits and regenerate only from the committed source evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/edge-program/profit_readiness.json"
PRODUCT = Path("docs/edge-program/product-charter.v1.json")
TRIAL_PROGRAM = Path("docs/forward-edge/trial-program.v1.json")
TRIAL_LEDGER = Path("docs/trials/ledger.jsonl")
PINS = Path("docs/forward-edge/protected-pins.json")
PROPOSAL = Path("docs/forward-edge/capital-risk-proposal-template.md")
APPROVED_MANDATE = Path("docs/edge-program/approved-execution-charter.v1.json")
FORBIDDEN_KEYS = {"percent", "percentage", "completion_percent", "progress_percent"}


def _bytes(path: Path, *, normalize_eol: bool = False) -> bytes:
    raw = (ROOT / path).read_bytes()
    return raw.replace(b"\r\n", b"\n") if normalize_eol else raw


def _evidence(path: Path, *, pointer: str | None = None, normalize_eol: bool = True) -> dict[str, str]:
    digest = hashlib.sha256(_bytes(path, normalize_eol=normalize_eol)).hexdigest()
    result = {"path": path.as_posix(), "sha256": digest}
    if pointer:
        result["json_pointer"] = pointer
    return result


def _verify_pins() -> tuple[bool, list[dict[str, str]]]:
    pins = json.loads(_bytes(PINS))
    evidence: list[dict[str, str]] = [_evidence(PINS)]
    okay = True
    for name, expected in pins["files"].items():
        path = Path(name)
        actual = hashlib.sha256(_bytes(path, normalize_eol=name in pins.get("normalize_eol", []))).hexdigest()
        evidence.append({"path": name, "sha256": actual})
        okay &= actual == expected
    return bool(okay), evidence


def build_report() -> dict[str, Any]:
    product = json.loads(_bytes(PRODUCT))
    program = json.loads(_bytes(TRIAL_PROGRAM))
    trials = [json.loads(line) for line in _bytes(TRIAL_LEDGER).decode("utf-8").splitlines() if line.strip()]
    trial_rows = [row for row in trials if row.get("kind") == "trial"]
    exhausted = any(row.get("status") == "EXHAUSTED" for row in program.get("programs", []))
    valid_pins, pin_evidence = _verify_pins()
    physical_disabled = product.get("execution_policy", {}).get("physical_orders_enabled") is False
    approved_mandate_exists = (ROOT / APPROVED_MANDATE).is_file()
    objective_demo = product.get("business_objective", {}).get("current_status") == "DEMONSTRATED"

    gates = [
        {
            "id": "prospective_net_profitability",
            "status": "GREEN" if objective_demo else "RED",
            "summary": "The product charter records prospective risk-adjusted net profitability as demonstrated." if objective_demo
                       else "Prospective risk-adjusted net profitability is explicitly not demonstrated.",
            "evidence": [_evidence(PRODUCT, pointer="/business_objective/current_status")],
        },
        {
            "id": "owner_approved_capital_limits",
            "status": "AMBER" if approved_mandate_exists else "RED",
            "summary": "An approval artifact exists but has not been independently validated by this report generator." if approved_mandate_exists
                       else "No owner-approved capital budget and hard-loss charter is recorded; the proposal is pending human review.",
            "evidence": [_evidence(PROPOSAL), _evidence(PRODUCT, pointer="/execution_policy/live_cash")],
        },
        {
            "id": "confirmatory_trial_capacity",
            "status": "RED" if exhausted else "AMBER",
            "summary": f"Protected confirmatory trial budget exhausted; {len(trial_rows)} trial rows are recorded." if exhausted
                       else f"Protected trial capacity requires review; {len(trial_rows)} trial rows are recorded.",
            "evidence": [_evidence(TRIAL_PROGRAM), _evidence(TRIAL_LEDGER, normalize_eol=True)],
        },
        {
            "id": "protected_research_integrity",
            "status": "GREEN" if valid_pins else "RED",
            "summary": "Protected research artifact hashes match." if valid_pins
                       else "One or more protected research artifact hashes do not match.",
            "evidence": pin_evidence,
        },
        {
            "id": "physical_order_safety",
            "status": "GREEN" if physical_disabled else "RED",
            "summary": "Physical order submission is disabled by the product charter." if physical_disabled
                       else "Physical-order disablement is not established by the product charter.",
            "evidence": [_evidence(PRODUCT, pointer="/execution_policy/physical_orders_enabled")],
        },
    ]
    rank = {"GREEN": 0, "AMBER": 1, "RED": 2}
    overall = max((gate["status"] for gate in gates), key=rank.__getitem__)
    report = {
        "schema": "fb.profit_readiness.v1",
        "overall": overall,
        "decision": "NOT_READY_FOR_PROFITABILITY_CLAIM" if overall == "RED" else "REVIEW_REQUIRED",
        "generated_from": {
            path.as_posix(): _evidence(path)["sha256"]
            for path in (PRODUCT, TRIAL_PROGRAM, TRIAL_LEDGER, PINS, PROPOSAL)
        },
        "gates": gates,
    }
    _reject_percentages(report)
    return report


def _reject_percentages(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in FORBIDDEN_KEYS or "percent" in key.lower():
                raise ValueError(f"hand-edited or percentage-based readiness is prohibited: {key}")
            _reject_percentages(child)
    elif isinstance(value, list):
        for child in value:
            _reject_percentages(child)


def render() -> str:
    return json.dumps(build_report(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def validate_output() -> None:
    if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != render():
        raise ValueError("profit readiness report is stale or hand-edited; regenerate it")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify committed output instead of writing")
    args = parser.parse_args()
    expected = render()
    if args.check:
        try:
            validate_output()
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        print("profit readiness report is current")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(expected, encoding="utf-8", newline="\n")
        print(f"wrote {OUTPUT.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
