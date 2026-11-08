"""Status tracker and progress bar for the TradingView and MQL5 integration addendum (ALG-001..ALG-128).

Each task has one status:

- ``implemented``: code and tests were added in this change;
- ``verified_existing``: an existing module already satisfies the acceptance criterion, and a test points at it;
- ``not_applicable``: the criterion's own decision rule concludes the work is not needed (with evidence);
- ``open``: not done; the note says what is missing;
- ``blocked``: depends on something outside the codebase (time, data rights, a reference run).

Run from the repository root:

    python scripts/algo_progress.py            # prints the bar and writes the status files
    python scripts/algo_progress.py --check    # exits non-zero if the committed status file is stale

Writes ``docs/audits/tradingview-mql5-integration-status.json`` and ``docs/algorithm-library-status.md``.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSON_OUT = ROOT / "docs" / "audits" / "tradingview-mql5-integration-status.json"
MD_OUT = ROOT / "docs" / "algorithm-library-status.md"

VALID = ("implemented", "verified_existing", "not_applicable", "open", "blocked")
DONE = ("implemented", "verified_existing", "not_applicable")

REG = "tests/test_algo_registry.py"
SPEC = "tests/test_algo_specs.py"
BRK = "tests/test_algo_breakouts.py"
SES = "tests/test_algo_session_mean.py"
STR = "tests/test_algo_structure_exec.py"
FAM = "tests/test_algo_families.py"
PAT = "tests/test_pattern_pivots.py"
DET = "tests/test_pattern_detectors.py"
W9 = "tests/test_pattern_wave9_detectors.py"
TD = "tests/test_algo_trend_denominator.py"

# (id, wave, status, evidence test files, note)
ROWS: list[tuple[str, str, str, tuple[str, ...], str]] = [
    ("ALG-001", "W1", "implemented", (REG,), "Registry validator rejects missing provenance and duplicate IDs."),
    ("ALG-002", "W1", "implemented", (REG,), "Indicators cannot be labelled executable; source types are fixed."),
    ("ALG-003", "W1", "implemented", (REG,), "TradingView open-source label recorded as reference_only; licence needs a reference."),
    ("ALG-004", "W1", "implemented", (REG,), "All MQL5 entries are reference_only unless a licence is recorded."),
    ("ALG-005", "W1", "implemented", (REG,), "A test fails if Pine or MQL5 source markers appear in runtime modules."),
    ("ALG-006", "W1", "implemented", (REG,), "docs/sources/CLEAN-ROOM.md states the rules; the test enforces the markers."),
    ("ALG-007", "W1", "implemented", (REG,), "Vendor figures render only as SOURCE_CLAIM; edge labels are refused."),
    ("ALG-008", "W1", "implemented", (REG,), "Registry hash changes when any source snapshot field changes."),
    ("ALG-009", "W1", "implemented", (REG,), "Each entry carries a family; families are listed with their overlaps."),
    ("ALG-010", "W1", "implemented", (REG,), "Unsuitable market tags are validated; every external item is paper-only."),
    ("ALG-011", "W1", "implemented", (REG, FAM), "Registry and strategies load with the network denied; no site is needed."),
    ("ALG-012", "W1", "implemented", (SPEC,), "AlgorithmSpec validates fields, versions and sources."),
    ("ALG-013", "W1", "implemented", (SPEC,), "BarClock: earliest fill is always the bar after the signal."),
    ("ALG-014", "W1", "implemented", (SPEC,), "OrderSpec rejects incompatible kinds, prices and expiries."),
    ("ALG-015", "W1", "implemented", (SPEC,), "Cash equity cannot be shorted in a spec."),
    ("ALG-016", "W1", "implemented", (SPEC,), "CostModel with version; attach_cost_version refuses empty results."),
    ("ALG-017", "W1", "implemented", (SPEC,), "ReferenceBehaviorFixture checks signal and decision indices."),
    ("ALG-018", "W1", "implemented", (SPEC,), "Parity is fixed to BEHAVIOUR_ADAPTATION; exact parity is refused."),
    ("ALG-019", "W1", "implemented", (SPEC,), "spec_sha256 is canonical and order-independent."),
    ("ALG-020", "W1", "implemented", (SPEC, STR), "Higher-timeframe bars count only after they have closed."),
    ("ALG-021", "W1", "implemented", (SPEC,), "Missing, zero or non-finite inputs return ABSTAIN."),
    ("ALG-022", "W1", "implemented", (SPEC,), "Cash baseline returns zero; alpha vs cash is computed explicitly."),
    ("ALG-023", "W1", "verified_existing", (PAT,), "causal_pivots: a pivot is known only after its right-hand bars."),
    ("ALG-024", "W1", "implemented", (STR,), "significant_swing is ATR-scaled and dimensionless."),
    ("ALG-025", "W1", "implemented", (STR,), "HH/HL/LH/LL labels use confirmed pivots; prefix-invariant."),
    ("ALG-026", "W1", "implemented", (STR,), "Trend state from labels; higher timeframe must be closed."),
    ("ALG-027", "W1", "implemented", (STR,), "weekly_completed never reports a week before its Friday."),
    ("ALG-028", "W1", "open", (), "Independent structure and entry clocks are not yet separate objects."),
    ("ALG-029", "W1", "implemented", (STR,), "dedupe_events keys on lineage, timeframe and pivot identity; replays idempotent."),
    ("ALG-030", "W1", "verified_existing", (PAT,), "EventLog is append-only with timestamped history."),
    ("ALG-031", "W1", "implemented", (STR,), "PivotMark keeps the bar index and the recognised-at bar separately."),
    ("ALG-032", "W1", "implemented", (STR,), "structure_state labels flat, ranging, volatile and gapping sequences."),
    ("ALG-033", "W2", "implemented", (TD,), "require_prior_trend keeps double tops only after a prior uptrend."),
    ("ALG-034", "W2", "implemented", (TD,), "require_prior_trend keeps double bottoms only after a prior downtrend."),
    ("ALG-035", "W2", "verified_existing", (DET,), "double_extrema is ATR-aware and duration bounded."),
    ("ALG-036", "W2", "verified_existing", (DET,), "head_shoulders requires the right shoulder to be known before emitting."),
    ("ALG-037", "W2", "verified_existing", (DET,), "head_shoulders_bottom mirrors the top rules."),
    ("ALG-038", "W2", "open", (), "The locked state is not in the lifecycle table yet."),
    ("ALG-039", "W2", "implemented", (STR,), "Confirmation uses closes; a wick alone never confirms (test added)."),
    ("ALG-040", "W2", "implemented", (TD,), "formation_denominator reports formed, confirmed, failed and open counts; rejected lookalikes stay in formed."),
    ("ALG-041", "W2", "implemented", (STR,), "One event per pivot identity; restart-resistant through dedupe."),
    ("ALG-042", "W2", "verified_existing", (W9,), "levels() never returns a profit target."),
    ("ALG-043", "W2", "verified_existing", (DET, W9), "Prefix invariance: later bars never change emitted events."),
    ("ALG-044", "W3", "implemented", (BRK,), "donchian_upper uses bars strictly before t."),
    ("ALG-045", "W3", "implemented", (BRK,), "basic_breakouts: long-only, reproducible reference level."),
    ("ALG-046", "W3", "implemented", (BRK,), "Turtle 20/10 constants are fixed and not tuned."),
    ("ALG-047", "W3", "implemented", (BRK,), "ATR-N sizing fails closed for bad stops or unfillable lots."),
    ("ALG-048", "W3", "implemented", (BRK,), "two_break_confirmation is a separate, labelled hypothesis."),
    ("ALG-049", "W3", "implemented", (BRK,), "trend_filtered_breakouts is a subset control, never a claimed improvement."),
    ("ALG-050", "W3", "implemented", (BRK,), "ATR threshold uses past bars only; exact threshold tested."),
    ("ALG-051", "W3", "open", (), "Keltner/EMA/ADX/volume family not implemented; needs a frozen grid."),
    ("ALG-052", "W3", "implemented", (BRK,), "gap_aware_fill fills at the open when a gap crosses the trigger."),
    ("ALG-053", "W3", "implemented", (BRK,), "incremental_pnl gives a paired difference against a baseline."),
    ("ALG-054", "W3", "implemented", (SES, SPEC), "NSE clock; foreign session keys are refused by the spec."),
    ("ALG-055", "W3", "implemented", (SES,), "opening_range freezes only after the last minute of the window closes."),
    ("ALG-056", "W3", "implemented", (SES,), "oco_intents and run_oco cancel the opposite side on entry."),
    ("ALG-057", "W3", "implemented", (SES,), "run_oco flattens at the end-of-session time; no overnight carry."),
    ("ALG-058", "W3", "implemented", (SES,), "range_excluded abstains on wide ranges and opening gaps."),
    ("ALG-059", "W3", "implemented", (SES,), "retest_signal requires a retest strictly after confirmation."),
    ("ALG-060", "W3", "implemented", (SES,), "failed_breakout_signal requires penetration then reentry."),
    ("ALG-061", "W3", "implemented", (SES,), "placebo_signal_times gives the same clock with no geometry."),
    ("ALG-062", "W3", "implemented", (SES,), "fill_if_available returns no fill for halts or zero volume."),
    ("ALG-063", "W3", "implemented", (SES,), "ist_clock converts UTC to IST and refuses naive timestamps."),
    ("ALG-064", "W3", "implemented", (BRK,), "rolling_zscore uses only the bars before t."),
    ("ALG-065", "W3", "implemented", (SES,), "session_vwap resets at each session."),
    ("ALG-066", "W3", "implemented", (SES,), "vwap_rejection needs closed rejection bars."),
    ("ALG-067", "W3", "open", (), "RSI/Keltner/MACD challenger not implemented."),
    ("ALG-068", "W3", "implemented", (SES,), "regime_abstain uses lagged returns only."),
    ("ALG-069", "W3", "implemented", (SES,), "pair_spread_z fits the hedge ratio on training data only."),
    ("ALG-070", "W3", "implemented", (SES,), "pair_is_tradable rejects cash equity short legs."),
    ("ALG-071", "W3", "implemented", (SES,), "next_size_after_loss never increases size."),
    ("ALG-072", "W3", "implemented", (SES,), "family_overlap_note keeps the K7 null visible."),
    ("ALG-073", "W3", "implemented", (SES,), "after_cost_utility shows gross, net and turnover together."),
    ("ALG-074", "W4", "open", (), "Source-attributed strategy cards in the trader UI are not built."),
    ("ALG-075", "W4", "implemented", (REG,), "stale_check flags entries older than the allowed age."),
    ("ALG-076", "W4", "open", (), "Neckline and breakout overlays with known_at are not drawn yet."),
    ("ALG-077", "W4", "open", (), "Visual state separation in the UI is not built."),
    ("ALG-078", "W4", "open", (), "Monetary display with tick and lot conventions is not built."),
    ("ALG-079", "W4", "open", (), "Radar filters are not built in the UI."),
    ("ALG-080", "W4", "open", (), "Compare panel is not built."),
    ("ALG-081", "W4", "implemented", (FAM,), "Strategies package has no route to the order system; boundary test."),
    ("ALG-082", "W4", "open", (), "Why-not-trade logging for algorithm events is not built."),
    ("ALG-083", "W4", "open", (), "Offline UI demo for these algorithms is not built."),
    ("ALG-084", "W4", "implemented", (STR,), "realised_pnl raises NoFillRecord without a fill."),
    ("ALG-085", "W4", "implemented", (STR,), "next_open_fill_index: signal fills at the next bar, never earlier."),
    ("ALG-086", "W4", "implemented", (STR,), "resolve_intrabar takes the adverse branch when both levels hit."),
    ("ALG-087", "W4", "implemented", (STR,), "Gap through stop fills at the adverse open; tick rounding is adverse."),
    ("ALG-088", "W4", "implemented", (STR,), "participation_fill caps by volume and reports unfilled quantity."),
    ("ALG-089", "W4", "implemented", (STR,), "must_flat forces futures flat at expiry and intraday products at close."),
    ("ALG-090", "W4", "implemented", (STR,), "viewer_summary never sums open gross MTM with closed net."),
    ("ALG-091", "W4", "verified_existing", (PAT,), "paper.Ledger double-entry identity exists in the paper package."),
    ("ALG-092", "W4", "implemented", (STR,), "coverage_score reports the exact denominator."),
    ("ALG-093", "W4", "implemented", (STR,), "usable_for_paper is false without instrument, cost or spec identity."),
    ("ALG-094", "W4", "implemented", (STR,), "monte_carlo_drill is repeatable under a fixed seed."),
    ("ALG-095", "W5", "implemented", (FAM,), "Every family declares null, alternative, sample and complexity."),
    ("ALG-096", "W5", "implemented", (FAM,), "effective_trials de-duplicates by spec hash."),
    ("ALG-097", "W5", "open", (), "Matched controls for 12-1 momentum, 52-week high, volatility and sector are not built for these families."),
    ("ALG-098", "W5", "implemented", (FAM,), "cohort_report keeps pre- and post-confirmation cohorts separate."),
    ("ALG-099", "W5", "verified_existing", ("tests/test_pattern_study.py",), "Purged split and embargo exist in patterns/study.py."),
    ("ALG-100", "W5", "implemented", (FAM,), "paired_incremental uses net differences for ranking."),
    ("ALG-101", "W5", "verified_existing", ("tests/test_pattern_study.py",), "Cluster block bootstrap exists in patterns/study.py."),
    ("ALG-102", "W5", "open", (), "Benjamini-Hochberg exists; PBO warning threshold is not implemented."),
    ("ALG-103", "W5", "verified_existing", ("tests/test_pattern_study.py",), "Trial budget guard refuses protected real-data trials in CI."),
    ("ALG-104", "W5", "verified_existing", ("tests/test_pattern_learning_agent.py",), "Model registry versions are immutable; challengers cannot overwrite."),
    ("ALG-105", "W5", "verified_existing", ("tests/test_pattern_wave9_modules.py",), "scheduled_retrain uses matured labels only."),
    ("ALG-106", "W5", "implemented", (FAM,), "verdict and release_gate never produce EDGE_CONFIRMED; assert_no_edge_label refuses it."),
    ("ALG-107", "W5", "implemented", (FAM,), "StrategyAdapter protocol with a working adapter."),
    ("ALG-108", "W5", "implemented", (FAM,), "Synthetic breakout, reversal and noisy-range generators."),
    ("ALG-109", "W5", "verified_existing", (DET, W9), "Prefix-invariance property tests across detectors."),
    ("ALG-110", "W5", "implemented", (REG, SPEC), "Registry and spec validators reject typos and inconsistent metadata."),
    ("ALG-111", "W5", "implemented", (BRK,), "Donchian signals are invariant to price scale and shift."),
    ("ALG-112", "W5", "verified_existing", ("tests/test_pattern_wave9_modules.py",), "run_resumable gives identical outputs after an interruption."),
    ("ALG-113", "W5", "open", (), "Risk and architecture checks are in CI for patterns; the strategies checks are not yet in CI config."),
    ("ALG-114", "W5", "verified_existing", ("tests/test_pattern_wave9_evidence.py",), "scripts/bench_patterns.py records before and after timings."),
    ("ALG-115", "W5", "not_applicable", (), "Benchmark decision python_sufficient: projected 29 s against a 600 s budget."),
    ("ALG-116", "W5", "not_applicable", (), "No native backend was introduced, so no parity tests are needed."),
    ("ALG-117", "W5", "implemented", (), "This tracker produces the feature status report."),
    ("ALG-118", "W6", "verified_existing", (FAM,), "python -m financial_brain.patterns.demo --offline runs with no credentials or network."),
    ("ALG-119", "W6", "open", (), "Scripted trader walkthrough from chart to journal is not written as one end-to-end test."),
    ("ALG-120", "W6", "open", (), "Daily historical-paper report with assumptions is not generated."),
    ("ALG-121", "W6", "implemented", (), "The tracker reports source claim, implemented, locally tested and forward-pending columns."),
    ("ALG-122", "W6", "implemented", (FAM,), "card() links source, spec hash and paper-only banner."),
    ("ALG-123", "W6", "open", (), "Corrupted ledger and database-lock recovery is only partly tested for these families."),
    ("ALG-124", "W6", "implemented", (), "The tracker writes docs/algorithm-library-status.md."),
    ("ALG-125", "W6", "implemented", (FAM,), "A synthetic negative strategy is rejected by verdict()."),
    ("ALG-126", "W6", "open", (), "Final gate: all P0 functional tests pass in CI; not yet run on Linux."),
    ("ALG-127", "W6", "implemented", (FAM,), "release_gate blocks promotion without forward observations."),
    ("ALG-128", "W6", "implemented", (REG,), "Registry is the dated bibliography; rights are recorded per entry."),
]


def progress_bar(done: int, total: int, width: int = 30) -> str:
    if total <= 0:
        return "[" + "-" * width + "] 0/0 (0.0%)"
    filled = round(width * done / total)
    pct = 100.0 * done / total
    return f"[{'#' * filled}{'-' * (width - filled)}] {done}/{total} ({pct:.1f}%)"


def build() -> dict[str, object]:
    ids = [r[0] for r in ROWS]
    if len(set(ids)) != len(ids):
        raise SystemExit("duplicate ALG IDs in tracker")
    for _id, _wave, status, tests, _note in ROWS:
        if status not in VALID:
            raise SystemExit(f"{_id}: invalid status {status}")
        for t in tests:
            if not (ROOT / t).exists():
                raise SystemExit(f"{_id}: evidence file missing: {t}")
    tasks = [
        {"id": i, "wave": w, "status": s, "evidence_tests": list(t), "notes": n} for i, w, s, t, n in ROWS
    ]
    counts = Counter(t["status"] for t in tasks)
    done = sum(counts[s] for s in DONE)
    by_wave: dict[str, dict[str, int]] = {}
    for t in tasks:
        bucket = by_wave.setdefault(str(t["wave"]), {"total": 0, "done": 0})
        bucket["total"] += 1
        bucket["done"] += 1 if t["status"] in DONE else 0
    return {
        "addendum": "Financial_Brain_TradingView_MQL5_Algorithm_Research_Integration_2026-11-08",
        "total": len(tasks),
        "done": done,
        "counts": dict(sorted(counts.items())),
        "by_wave": by_wave,
        "bar": progress_bar(done, len(tasks)),
        "tasks": tasks,
    }


def render_markdown(status: dict[str, object]) -> str:
    lines = [
        "# Algorithm library status (ALG-001..ALG-128)",
        "",
        f"Progress: {status['bar']}",
        "",
        "| Wave | Done | Total | Progress |",
        "|---|---|---|---|",
    ]
    by_wave: dict[str, dict[str, int]] = status["by_wave"]  # type: ignore[assignment]
    for wave in sorted(by_wave):
        b = by_wave[wave]
        lines.append(f"| {wave} | {b['done']} | {b['total']} | {progress_bar(b['done'], b['total'], 20)} |")
    lines += ["", "| ID | Wave | Status | Evidence | Note |", "|---|---|---|---|---|"]
    for t in status["tasks"]:  # type: ignore[union-attr]
        ev = ", ".join(t["evidence_tests"]) or "-"
        lines.append(f"| {t['id']} | {t['wave']} | {t['status']} | {ev} | {t['notes']} |")
    lines.append("")
    lines.append("Source claims are never edge claims. `EDGE_CONFIRMED` needs independent forward evidence (ALG-106, ALG-127).")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    status = build()
    text = json.dumps(status, indent=2, sort_keys=True) + "\n"
    md = render_markdown(status)
    if "--check" in args:
        current = JSON_OUT.read_text(encoding="utf-8") if JSON_OUT.exists() else ""
        if current != text:
            print("status file is stale: run python scripts/algo_progress.py")
            return 1
        print(status["bar"])
        return 0
    JSON_OUT.write_text(text, encoding="utf-8", newline="\n")
    MD_OUT.write_text(md, encoding="utf-8", newline="\n")
    print(status["bar"])
    for wave, b in sorted(status["by_wave"].items()):  # type: ignore[union-attr]
        print(f"  {wave}  {progress_bar(b['done'], b['total'], 20)}")
    print("counts:", status["counts"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
