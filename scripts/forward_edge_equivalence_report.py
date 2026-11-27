"""Generate the fixed-fixture legacy-to-integrated paper runner comparison."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from financial_brain.forward_edge import pipeline
from financial_brain.forward_edge.fixtures import EXPECTED, fixture_pack

GOLDEN_PATH = ROOT / "tests/fixtures/forward_edge/golden_signatures.json"
OUTPUT = ROOT / "docs/edge-program/forward-edge-equivalence-report.v1.json"


def build_report() -> dict:
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    rows = []
    for name, fixture in sorted(fixture_pack().items()):
        fixture_bytes = json.dumps(fixture, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        fixture_sha256 = hashlib.sha256(fixture_bytes).hexdigest()
        legacy = pipeline.run_session(fixture)
        integrated = legacy["forward_runner"]
        replayed = pipeline.run_session(fixture)
        replayed_integrated = replayed["forward_runner"]
        expected = EXPECTED[name]
        legacy_execution_rejections = []
        if legacy["reason"].startswith("GATE:"):
            legacy_execution_rejections = legacy["reason"].removeprefix("GATE:").split("+")
        elif legacy["reason"] in {"PRICE_BAND", "FIRST_QUOTE_AFTER_MAX_WAIT", "STALE_QUOTE"}:
            legacy_execution_rejections = [legacy["reason"]]
        integrated_execution_rejections = sorted({
            str(trace["reject_reason"] or trace["status"])
            for trace in replayed_integrated.get("execution_trace", [])
            if trace.get("reject_reason") or trace.get("status") in {"REJECTED", "MISSED"}
        })
        if not replayed_integrated["execution_trace"] and replayed_integrated["decision_trace"]:
            integrated_execution_rejections = sorted({
                str(code) for decision in replayed_integrated["decision_trace"]
                for code in decision.get("reason_codes", [])
            })
        same_input_replay = (
            replayed["signature"] == legacy["signature"]
            and replayed_integrated["records_sha256"] == integrated["records_sha256"]
            and replayed_integrated["decision_trace"] == integrated["decision_trace"]
            and replayed_integrated["risk_trace"] == integrated["risk_trace"]
        )
        legacy_match = legacy["outcome"] == expected and legacy["signature"] == golden[name]
        integrated_match = integrated["economic_outcome"] == expected
        differences: list[str] = []
        if not legacy_match:
            differences.append("Legacy outcome or frozen signature differs from the fixture oracle.")
        if not integrated_match:
            differences.append(
                f"Integrated journal outcome {integrated['economic_outcome']} differs from fixture contract {expected}."
            )
        if not same_input_replay:
            differences.append("Identical ordered fixture input did not reproduce the same legacy signature, integrated journal hash, decision trace, and risk trace.")
        rejection_comparison = {
            "legacy_execution_rejection_codes": legacy_execution_rejections,
            "integrated_execution_rejection_codes": integrated_execution_rejections,
            "matched": legacy_execution_rejections == integrated_execution_rejections,
            "scope": "fixture execution and decision rejection reasons; separate risk policy is not exercised",
        }
        if not rejection_comparison["matched"]:
            differences.append(
                "Execution rejection taxonomy differs across adapters; review the recorded legacy/integrated codes before claiming replay parity."
            )
        if integrated["report_status"] == "INCOMPLETE":
            differences.append(
                "Integrated session is INCOMPLETE because an order outcome is unresolved; this is intentional fail-closed handling."
            )
        if not integrated["net_pnl_eligible"] and expected in {"WIN", "LOSS"}:
            differences.append("Integrated accounting does not mark this result eligible for net-P&L evaluation.")
        if name == "no_trade":
            differences.append(
                "No-trade parity is explicitly checked: legacy NO_TRADE maps to integrated NO_TRADE, with zero paper intents and no physical orders."
            )
        rows.append({
            "fixture": name,
            "input_contract_sha256": fixture_sha256,
            "same_ordered_input_replay_matches": same_input_replay,
            "rejection_comparison": rejection_comparison,
            "expected_contract_outcome": expected,
            "legacy_pipeline": {
                "outcome": legacy["outcome"], "reason": legacy["reason"],
                "signature": legacy["signature"], "matches_frozen_golden": legacy_match,
            },
            "integrated_paper_journal": {
                "economic_outcome": integrated["economic_outcome"],
                "report_status": integrated["report_status"],
                "paper_intent_count": integrated["paper_intent_count"],
                "receipt_count": integrated["receipt_count"],
                "physical_orders": integrated["physical_orders"],
                "ledger_balanced": integrated["ledger_balanced"],
                "cash_reconciled": integrated["cash_reconciled"],
                "net_pnl_eligible": integrated["net_pnl_eligible"],
                "realized_net_pnl_inr": integrated["realized_net_pnl_inr"],
                "records_sha256": integrated["records_sha256"],
                "matches_fixture_contract": integrated_match,
            },
            "divergences_and_explanations": differences,
        })
    return {
        "schema": "fb.forward_edge.fixture_equivalence_report.v1",
        "scope": (
            "Deterministic synthetic fixture compatibility only. Compares the current legacy fixture pipeline and "
            "its integrated canonical paper journal against fixed fixture outcomes and golden signatures. It does "
            "not compare historical source revisions, live market sessions, or strategy performance."
        ),
        "source_check": {
            "golden_signatures_sha256": hashlib.sha256(
                GOLDEN_PATH.read_bytes().replace(b"\r\n", b"\n")
            ).hexdigest(),
            "fixtures": len(rows),
            "all_legacy_fixture_contracts_match": all(row["legacy_pipeline"]["matches_frozen_golden"] for row in rows),
            "all_integrated_fixture_contracts_match": all(row["integrated_paper_journal"]["matches_fixture_contract"] for row in rows),
            "all_same_ordered_input_replays_match": all(row["same_ordered_input_replay_matches"] for row in rows),
            "all_rejection_codes_match": all(row["rejection_comparison"]["matched"] for row in rows),
            "all_physical_orders_disabled": all(not row["integrated_paper_journal"]["physical_orders"] for row in rows),
            "no_trade_fixture_has_zero_intents": next(row for row in rows if row["fixture"] == "no_trade")["integrated_paper_journal"]["paper_intent_count"] == 0,
        },
        "fixtures": rows,
        "limitations": [
            "The frozen legacy oracle stores SHA-256 signatures, not complete prior-version decision records, so historic field-by-field migration diffs cannot be reconstructed from it.",
            "Rejection comparisons cover the shared fixture contract and execution/decision rejection taxonomy; fixtures currently use an allowed integrated risk verdict, so independent legacy-vs-integrated risk-policy parity is not established.",
            "The fixtures are synthetic and establish software compatibility, not market edge, profitability, or execution fidelity.",
            "A future strategy migration needs archived versioned inputs and full decision outputs for each old and new implementation.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the committed report is stale")
    args = parser.parse_args(argv)
    expected = json.dumps(build_report(), indent=2, sort_keys=True) + "\n"
    if args.check:
        try:
            actual = OUTPUT.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"equivalence report unavailable: {exc}", file=sys.stderr)
            return 1
        if actual != expected:
            print("fixture equivalence report is stale; run python scripts/forward_edge_equivalence_report.py", file=sys.stderr)
            return 1
        print("Forward-edge fixture equivalence report matches current fixtures and runners")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(expected, encoding="utf-8", newline="\n")
    print(f"Wrote {OUTPUT.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
