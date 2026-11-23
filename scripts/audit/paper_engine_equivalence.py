"""Generate a deterministic legacy-versus-integrated paper fixture report."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False, default=str).encode("utf-8")


def build_report(root: Path) -> dict[str, Any]:
    from financial_brain.forward_edge.fixtures import EXPECTED, fixture_pack
    from financial_brain.forward_edge.pipeline import run_session

    golden = json.loads((root / "tests/fixtures/forward_edge/golden_signatures.json").read_text(encoding="utf-8"))
    fixtures = fixture_pack()
    cases: list[dict[str, Any]] = []
    for name in sorted(fixtures):
        fixture = fixtures[name]
        legacy = run_session(fixture)
        integrated = legacy["forward_runner"]
        if legacy["outcome"] != EXPECTED[name]:
            raise AssertionError(f"legacy fixture oracle mismatch for {name}")
        if legacy["signature"] != golden[name]:
            raise AssertionError(f"legacy golden signature mismatch for {name}")

        divergences: list[str] = []
        if legacy["outcome"] != integrated["economic_outcome"]:
            divergences.append(
                f"classification: legacy={legacy['outcome']}, integrated={integrated['economic_outcome']}"
            )
        legacy_net = Decimal(str(legacy["net_pnl"]))
        integrated_net = (Decimal(str(integrated["realized_net_pnl_inr"]))
                          if integrated["realized_net_pnl_inr"] is not None else None)
        legacy_paise = legacy_net.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        integrated_paise = (integrated_net.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
                            if integrated_net is not None else None)
        pnl_match_at_paise = (legacy_paise == integrated_paise if integrated_paise is not None
                              else legacy_paise == 0 and not integrated["net_pnl_eligible"])
        if not pnl_match_at_paise:
            divergences.append(
                "net P&L differs at paise precision: "
                f"legacy={legacy_paise}, integrated={integrated_paise}"
            )
        known_differences: list[str] = []
        if name == "adverse_fill":
            known_differences.append(
                "adverse-selection accounting differs: legacy shifts the entry fill before calculating brokerage "
                "(INR 4.509 rounds to 4.51), while the integrated path keeps the INR 1,500 limit fill and "
                "books INR 30 as a separate adverse-selection charge (brokerage INR 4.50); this yields a "
                "one-paise net difference after the exit-fee rounding/precision difference"
            )
        if integrated_net is not None and integrated_net != legacy_net:
            known_differences.append(
                "raw sub-paise values differ; the legacy engine rounds each fee and final P&L to paise, "
                "while the integrated projection retains Decimal fractions below one paise"
            )
        elif integrated_net is None and legacy_paise == 0:
            known_differences.append(
                "legacy reports zero net P&L for no realized trade; integrated accounting uses null "
                "when no realized position exists"
            )
        if integrated["report_status"] != "CLOSED":
            known_differences.append(
                f"lifecycle completeness: integrated report is {integrated['report_status']}"
            )
        if legacy["outcome"] == "NO_TRADE" and (
            integrated["paper_intent_count"] or integrated["receipt_count"]
        ):
            divergences.append(
                "no-trade safety: integrated path created paper intents or execution receipts"
            )

        cases.append({
            "fixture_id": name,
            "session_id": fixture["session_id"],
            "fixture_sha256": hashlib.sha256(canonical_bytes(fixture)).hexdigest(),
            "legacy": {
                "outcome": legacy["outcome"],
                "reason": legacy["reason"],
                "net_pnl_inr": legacy["net_pnl"],
                "golden_signature": legacy["signature"],
            },
            "integrated": {
                "outcome": integrated["economic_outcome"],
                "report_status": integrated["report_status"],
                "net_pnl_inr": integrated["realized_net_pnl_inr"],
                "decision_count": integrated["decision_count"],
                "paper_intent_count": integrated["paper_intent_count"],
                "receipt_count": integrated["receipt_count"],
                "net_pnl_eligible": integrated["net_pnl_eligible"],
                "runner_records_sha256": integrated["records_sha256"],
            },
            "classification_match": legacy["outcome"] == integrated["economic_outcome"],
            "net_pnl_match_at_paise": pnl_match_at_paise,
            "known_differences": known_differences,
            "divergences": divergences,
        })

    matches = sum(case["classification_match"] for case in cases)
    independent = False  # fixture_adapter records the decision supplied by run_session
    return {
        "schema": "fb.paper-engine-equivalence.v1",
        "scope": "Eight deterministic synthetic fixture sessions; compares legacy golden outcomes with the integrated ForwardSessionRunner projection.",
        "status": "PARTIAL",
        "status_reason": "The integrated fixture adapter is passed the legacy outcome; an independent run of each original strategy through both engines is not established.",
        "independent_strategy_reruns": independent,
        "classification_matches": matches,
        "classification_divergences": len(cases) - matches,
        "net_pnl_matches_at_paise": sum(case["net_pnl_match_at_paise"] for case in cases),
        "case_count": len(cases),
        "cases": cases,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Paper-engine fixture equivalence report",
        "",
        f"Status: **{report['status']}** — {report['status_reason']}",
        "",
        f"Fixed sessions: {report['case_count']}; matching outcome labels: "
        f"{report['classification_matches']}; label divergences: {report['classification_divergences']}; "
        f"INR-paise P&L matches: {report['net_pnl_matches_at_paise']}.",
        "",
        "| Fixture | Legacy outcome | Integrated outcome | Integrated report | Legacy net P&L (INR) | Integrated realized net P&L (INR) | Intents / receipts | Divergence notes |",
        "|---|---|---|---|---:|---:|---:|---|",
    ]
    for case in report["cases"]:
        legacy, integrated = case["legacy"], case["integrated"]
        notes = "; ".join(case["known_differences"] + case["divergences"])
        notes = notes or "No recorded difference in the compared fields"
        notes = notes.replace("|", "\\|")
        lines.append(
            f"| {case['fixture_id']} | {legacy['outcome']} | {integrated['outcome']} | "
            f"{integrated['report_status']} | {legacy['net_pnl_inr']} | {integrated['net_pnl_inr']} | "
            f"{integrated['paper_intent_count']} / {integrated['receipt_count']} | {notes} |"
        )
    lines.extend([
        "",
        "## Interpretation",
        "",
        "Outcome labels are checked against `tests/fixtures/forward_edge/golden_signatures.json` and the fixed `fixture_pack()`. The integrated adapter receives the legacy result and uses it to record the fixture decision; therefore matching labels are a plumbing/regression check, not an independent strategy rerun or evidence of alpha.",
        "",
        "P&L is compared at INR paise precision using half-even rounding. The legacy engine rounds its fees and final P&L to paise; the integrated projection preserves cost values below one paise after converting the cost model's floating-point output to Decimal. The adverse-selection fixture also has a different brokerage basis: legacy reprices the fill before computing brokerage, while integrated execution books adverse selection as a separate cost. For non-trades, legacy returns zero while integrated accounting uses null for no realized posting. These differences are listed per case. An `UNKNOWN_ORDER` intentionally remains incomplete and ineligible for net P&L.",
        "",
        "Generated by `python scripts/audit/paper_engine_equivalence.py`; all market inputs are synthetic fixtures.",
        "",
    ])
    return "\n".join(lines)


def outputs(root: Path) -> tuple[bytes, bytes]:
    report = build_report(root)
    json_bytes = (json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
    markdown_bytes = render_markdown(report).encode("utf-8")
    return json_bytes, markdown_bytes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail when committed report artifacts are stale")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    json_data, markdown_data = outputs(root)
    targets = {
        root / "docs/edge-program/paper-engine-equivalence.v1.json": json_data,
        root / "docs/edge-program/PAPER_ENGINE_EQUIVALENCE.md": markdown_data,
    }
    stale = [str(path.relative_to(root)) for path, data in targets.items()
             if not path.is_file() or path.read_bytes() != data]
    if args.check:
        if stale:
            raise SystemExit("stale paper-engine equivalence report: " + ", ".join(stale))
        print("paper-engine equivalence report is current")
        return 0
    for path, data in targets.items():
        path.write_bytes(data)
    print("wrote paper-engine equivalence report: " + ", ".join(str(p.relative_to(root)) for p in targets))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
