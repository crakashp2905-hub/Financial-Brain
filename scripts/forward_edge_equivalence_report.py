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
        legacy = pipeline.run_session(fixture)
        integrated = legacy["forward_runner"]
        expected = EXPECTED[name]
        legacy_match = legacy["outcome"] == expected and legacy["signature"] == golden[name]
        integrated_match = integrated["economic_outcome"] == expected
        differences: list[str] = []
        if not legacy_match:
            differences.append("Legacy outcome or frozen signature differs from the fixture oracle.")
        if not integrated_match:
            differences.append(
                f"Integrated journal outcome {integrated['economic_outcome']} differs from fixture contract {expected}."
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
            "golden_signatures_sha256": hashlib.sha256(GOLDEN_PATH.read_bytes()).hexdigest(),
            "fixtures": len(rows),
            "all_legacy_fixture_contracts_match": all(row["legacy_pipeline"]["matches_frozen_golden"] for row in rows),
            "all_integrated_fixture_contracts_match": all(row["integrated_paper_journal"]["matches_fixture_contract"] for row in rows),
            "all_physical_orders_disabled": all(not row["integrated_paper_journal"]["physical_orders"] for row in rows),
            "no_trade_fixture_has_zero_intents": next(row for row in rows if row["fixture"] == "no_trade")["integrated_paper_journal"]["paper_intent_count"] == 0,
        },
        "fixtures": rows,
        "limitations": [
            "The frozen legacy oracle stores SHA-256 signatures, not complete prior-version decision records, so historic field-by-field migration diffs cannot be reconstructed from it.",
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
