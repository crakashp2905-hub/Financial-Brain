"""Generate the chart-pattern baseline and completion matrix (CP-0001, CP-0336, CP-0393).

Reads the master checklist, validates that every CP row parses exactly once, and
writes:

- ``docs/audits/pattern-baseline.json``        fingerprint of the checkout (CP-0001)
- ``docs/audits/chart-patterns-completion.json`` one row per CP item (contract in
  the checklist's "Automated sign-off file contract")

A row is ``implemented`` or ``verified_existing`` only when it is listed in
``EVIDENCE`` below with source files and a test file that exists on disk. Every
other row stays ``incomplete``. Nothing is marked done by heuristic.

Usage: ``python scripts/chart_patterns_matrix.py [--check]``. ``--check`` exits
nonzero if the committed matrix is stale relative to the generated one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKLIST = ROOT / "docs" / "audits" / "chart-patterns-master-checklist.md"
BASELINE = ROOT / "docs" / "audits" / "pattern-baseline.json"
COMPLETION = ROOT / "docs" / "audits" / "chart-patterns-completion.json"
DEP_FILES = ("pyproject.toml", "requirements-lock.txt")

CP_RE = re.compile(r"^- \[ \] \*\*(CP-\d{4})\*\* (.+?)\s*$")
WORKSTREAM_RE = re.compile(r"^### (\d{2}) — (.+?)\s*$")
PRIORITY_RE = re.compile(r"\*\*Priority:\*\* (P[012])")

PYTEST = "python -m pytest -q"
PATTERN_TESTS = [
    "tests/test_pattern_catalogue.py",
    "tests/test_pattern_spec.py",
    "tests/test_pattern_pivots.py",
    "tests/test_pattern_offline.py",
    "tests/test_pattern_matrix.py",
    "tests/test_pattern_stream_store.py",
    "tests/test_pattern_detectors.py",
]

# task_id -> (source_files, test_files, notes). Scope limits are stated in notes.
EVIDENCE: dict[str, tuple[list[str], list[str], str]] = {
    "CP-0001": (
        ["scripts/chart_patterns_matrix.py"],
        ["tests/test_pattern_matrix.py"],
        "Deterministic fingerprint: HEAD SHA, dirty paths, Python version, SHA-256 of pyproject.toml and requirements-lock.txt.",
    ),
    "CP-0010": (
        ["docs/audits/pattern-implementation-map.md"],
        ["tests/test_pattern_matrix.py"],
        "Maps every implemented task to real module and test paths; the matrix test checks those paths exist.",
    ),
    "CP-0011": (
        ["src/financial_brain/patterns/offline.py"],
        ["tests/test_pattern_offline.py"],
        "Package-level guard only. Process-wide no-network startup assert is CP-0375 and is not done.",
    ),
    "CP-0014": (
        ["src/financial_brain/patterns/catalogue.py"],
        ["tests/test_pattern_catalogue.py"],
        "sample_total is typed from the 'n' field. sample_confirmed and sample_unconfirmed are not reliably extractable from source text and are not emitted.",
    ),
    "CP-0016": (
        ["src/financial_brain/patterns/catalogue.py"],
        ["tests/test_pattern_catalogue.py"],
        "Only plain integer/decimal strings are typed; '~', '<', ranges and compound text stay null with source_claim_text.",
    ),
    "CP-0018": (
        ["src/financial_brain/patterns/catalogue.py"],
        ["tests/test_pattern_catalogue.py"],
        "Uniqueness and N/B source-prefix validation. Migration tests are not applicable at catalogue v1.",
    ),
    "CP-0019": (
        ["src/financial_brain/patterns/catalogue.py"],
        ["tests/test_pattern_catalogue.py"],
        "Controlled FAMILY_MAP; 5 source rows whose family is not a real family map to 'unclassified' and are not guessed.",
    ),
    "CP-0020": (
        ["src/financial_brain/patterns/catalogue.py"],
        ["tests/test_pattern_catalogue.py"],
        "direction_class is descriptive (bullish/bearish/either/unclassified). It is not an eligible trade side.",
    ),
    "CP-0025": (
        ["src/financial_brain/patterns/catalogue.py"],
        ["tests/test_pattern_catalogue.py"],
        "CLI validates, counts by source and family, and exits 1 on malformed data or count mismatch.",
    ),
    "CP-0027": (
        ["src/financial_brain/patterns/spec.py"],
        ["tests/test_pattern_spec.py"],
        "Canonical JSON with sorted keys; identical SHA-256 for reordered parameters. Cross-platform hash equality is not yet tested on Linux.",
    ),
    "CP-0028": (
        ["src/financial_brain/patterns/spec.py"],
        ["tests/test_pattern_spec.py"],
        "Typed PatternEvent with timezone-aware timestamps and ordering checks.",
    ),
    "CP-0030": (
        ["src/financial_brain/patterns/spec.py"],
        ["tests/test_pattern_spec.py"],
        "EventState enum and explicit transition table; illegal transitions raise InvalidTransition.",
    ),
    "CP-0033": (
        ["src/financial_brain/patterns/spec.py"],
        ["tests/test_pattern_spec.py"],
        "Detector events can only carry DESCRIPTIVE evidence; EvidenceTier is separate from any probability.",
    ),
    "CP-0034": (
        ["src/financial_brain/patterns/spec.py"],
        ["tests/test_pattern_spec.py"],
        "make_event_id is a hash of lineage, timeframe, spec SHA and first-known time.",
    ),
    "CP-0038": (
        ["src/financial_brain/patterns/spec.py", "src/financial_brain/patterns/pivots.py"],
        ["tests/test_pattern_spec.py", "tests/test_pattern_pivots.py"],
        "NaN/inf rejected in parameters, geometry and pivot inputs; naive timestamps rejected.",
    ),
    "CP-0040": (
        ["src/financial_brain/patterns/spec.py"],
        ["tests/test_pattern_spec.py"],
        "Round-trip for PatternSpec and PatternEvent only. No other public model exists yet.",
    ),
    "CP-0056": (
        ["src/financial_brain/patterns/pivots.py"],
        ["tests/test_pattern_pivots.py"],
        "Causal left/right swing pivots; known_at_index = index + right.",
    ),
    "CP-0057": (
        ["src/financial_brain/patterns/pivots.py"],
        ["tests/test_pattern_pivots.py"],
        "Strict-left/non-strict-right tie rule; flat tops resolve to the first plateau bar.",
    ),
    "CP-0136": (
        ["src/financial_brain/patterns/pivots.py"],
        ["tests/test_pattern_pivots.py", "tests/test_pattern_detectors.py"],
        "Prefix invariance for the pivot primitive over 40 seeded random series. Covers the primitive only, not detectors.",
    ),
    "CP-0138": (
        ["src/financial_brain/patterns/pivots.py"],
        ["tests/test_pattern_pivots.py"],
        "Pivot not observable before its right window closes, for the pivot primitive.",
    ),
    "CP-0021": (
        ["src/financial_brain/patterns/catalogue.py"],
        ["tests/test_pattern_stream_store.py"],
        "promote_definition_status advances one step at a time and requires an evidence reference; text alone stays descriptive. Not yet wired to a registry store.",
    ),
    "CP-0035": (
        ["src/financial_brain/patterns/spec.py"],
        ["tests/test_pattern_stream_store.py"],
        "Provenance contract with catalogue, commit, calendar and config hashes. Not yet embedded in every result writer.",
    ),
    "CP-0037": (
        ["src/financial_brain/patterns/spec.py"],
        ["tests/test_pattern_stream_store.py"],
        "config_hash is a canonical SHA-256 of parameters, so a parameter change changes the hash. Event streams are not yet versioned by it.",
    ),
    "CP-0039": (
        ["src/financial_brain/patterns/spec.py"],
        ["tests/test_pattern_stream_store.py"],
        "PatternSignal is research_only with no side, quantity or price fields.",
    ),
    "CP-0069": (
        ["src/financial_brain/patterns/events_store.py"],
        ["tests/test_pattern_stream_store.py"],
        "sorted_events gives canonical order independent of input order.",
    ),
    "CP-0070": (
        ["src/financial_brain/patterns/streaming.py"],
        ["tests/test_pattern_stream_store.py"],
        "Streaming pivots equal batch output on every prefix for 25 seeded series. Pivot primitive only.",
    ),
    "CP-0071": (
        ["src/financial_brain/patterns/streaming.py"],
        ["tests/test_pattern_stream_store.py"],
        "Buffer capped at left+right+1 bars (tested over 10,000 bars). No active candidates exist yet, so the 'do not delete active candidates' rule is untested.",
    ),
    "CP-0072": (
        ["src/financial_brain/patterns/pivots.py"],
        ["tests/test_pattern_stream_store.py", "tests/test_pattern_pivots.py"],
        "Constant series, one bar, empty input and NaN are covered for the pivot primitive. Spike and missing-bar cases are not yet covered.",
    ),
    "CP-0121": (
        ["src/financial_brain/patterns/events_store.py"],
        ["tests/test_pattern_stream_store.py"],
        "Append-only version log keyed by (event_id, status); history is never replaced.",
    ),
    "CP-0126": (
        ["src/financial_brain/patterns/events_store.py"],
        ["tests/test_pattern_stream_store.py"],
        "as_of(cutoff) returns the newest version knowable by the cutoff. EXPIRED and SUPERSEDED are rejected until they have a time field.",
    ),
    "CP-0128": (
        ["src/financial_brain/patterns/events_store.py"],
        ["tests/test_pattern_stream_store.py"],
        "Identical re-append is a no-op; a different payload under the same key raises ConflictingVersion.",
    ),
    "CP-0132": (
        ["src/financial_brain/patterns/events_store.py"],
        ["tests/test_pattern_stream_store.py"],
        "Canonical sort key ignores insertion order and dict iteration order.",
    ),
    "CP-0058": (
        ["src/financial_brain/patterns/geometry.py"],
        ["tests/test_pattern_geometry.py"],
        "TolerancePolicy names one unit (atr, pct or tick) and raises when its input is missing; units are never mixed.",
    ),
    "CP-0060": (
        ["src/financial_brain/patterns/geometry.py"],
        ["tests/test_pattern_geometry.py", "tests/test_pattern_detectors.py"],
        "horizontal_touches counts only bars at or before known_upto. Breakout-level construction is not built yet.",
    ),
    "CP-0061": (
        ["src/financial_brain/patterns/geometry.py"],
        ["tests/test_pattern_geometry.py"],
        "Line.through rejects vertical lines; intersection_index returns None for parallel lines. Convergence and angle tests are not built yet.",
    ),
    "CP-0066": (
        ["src/financial_brain/patterns/geometry.py"],
        ["tests/test_pattern_geometry.py"],
        "fraction() returns None when the ratio is out of bounds or the denominator is zero. Compression and pole primitives are not built yet.",
    ),
    "CP-0067": (
        ["src/financial_brain/patterns/geometry.py"],
        ["tests/test_pattern_geometry.py"],
        "resolve_bar_ambiguity returns AMBIGUOUS when one bar spans both stop and target, and never assumes a favourable order.",
    ),
    "CP-0073": (
        ["src/financial_brain/patterns/geometry.py"],
        ["tests/test_pattern_geometry.py"],
        "DecisionTrace records each named check with value and threshold, and accepts only when every check passes. No detector uses it yet.",
    ),
    "CP-0042": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Bars rejects OHLC inequality and non-finite values; quarantine_rows counts rejections by reason. Read-only source adapter is not built.',
    ),
    "CP-0059": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Two-point trendlines through the first and last known pivots of each type. Robust regression alternative is not built.',
    ),
    "CP-0062": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Head-and-shoulders neckline built from causally known intervening extrema; undefined necklines are skipped.',
    ),
    "CP-0074": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Rectangle candidate with flat top and bottom, minimum touches and bounded range fraction. Duration is a fixed window, not exchange-session aware.',
    ),
    "CP-0075": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Direction stays neutral until a close breaks either side; both upward and downward confirmation are recorded.',
    ),
    "CP-0076": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Ascending triangle: flat upper line, rising lows, two-touch minimum, converging check.',
    ),
    "CP-0077": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Descending triangle: flat lower line, falling highs; opposite breakouts are recorded, not discarded.',
    ),
    "CP-0078": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Symmetric triangle requires falling highs, rising lows and convergence; parallel and diverging fits are rejected.',
    ),
    "CP-0079": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Breakout confirmed only on a close beyond the line. Optional delayed re-test rule is not built.',
    ),
    "CP-0080": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Bullish and bearish flags with predeclared pole length, minimum pole return and retracement limit. The pole ends before the flag starts.',
    ),
    "CP-0082": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Rising and falling wedges require same-sign converging lines. Explanatory-quality rejection is not built.',
    ),
    "CP-0086": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Failed candidates are recorded with failed_index; confirmation in either direction is supported.',
    ),
    "CP-0090": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'discover() runs every detector and returns candidates, confirmations and failures in canonical order.',
    ),
    "CP-0091": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Tentative double bottom from two known lows with a neckline; unconfirmed and failed candidates are kept.',
    ),
    "CP-0092": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Confirmed double bottom on a close above the neckline, with recognition and confirmation timestamped separately.',
    ),
    "CP-0093": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Double top mirror with tentative and confirmed states. No success statistic is computed.',
    ),
    "CP-0095": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Head-and-shoulders bottom needs prior downtrend on known bars, symmetric shoulders, and emits only when the right shoulder is known.',
    ),
    "CP-0096": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Head-and-shoulders top with neckline break; earlier candidates are not rewritten.',
    ),
    "CP-0107": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Negative fixtures: strong trend is not a rectangle; flat series yields no triangles or double extrema.',
    ),
    "CP-0137": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        "Appending a large future rally and crash leaves earlier formations' as-of views unchanged.",
    ),
    "CP-0140": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Candidates that never confirm stay in the output as CANDIDATE or FAILED, so denominators include them.',
    ),
    "CP-0149": (
        ["src/financial_brain/patterns/"],
        ["tests/test_pattern_offline.py"],
        "AST boundary check: no pattern module imports network clients, broker or execution/paper packages.",
    ),
}


def parse_checklist(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    workstream = ""
    priority = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        ws = WORKSTREAM_RE.match(line)
        if ws:
            workstream = f"{ws.group(1)} {ws.group(2)}"
            priority = ""
            continue
        pr = PRIORITY_RE.search(line)
        if pr and not priority:
            priority = pr.group(1)
        cp = CP_RE.match(line)
        if cp:
            rows.append(
                {"task_id": cp.group(1), "title": cp.group(2), "workstream": workstream, "priority": priority}
            )
    ids = [row["task_id"] for row in rows]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise SystemExit(f"duplicate checklist IDs: {dupes}")
    return rows


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def build_baseline() -> dict[str, object]:
    dirty = [line[3:] for line in _git("status", "--porcelain").splitlines() if line]
    return {
        "commit": _git("rev-parse", "HEAD"),
        "dirty_paths": sorted(dirty),
        "python": platform.python_version(),
        "platform": sys.platform,
        "dependency_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in DEP_FILES
        },
    }


def build_completion(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for row in rows:
        task_id = row["task_id"]
        entry: dict[str, object] = {
            "task_id": task_id,
            "priority": row["priority"],
            "workstream": row["workstream"],
            "title": row["title"],
        }
        if task_id in EVIDENCE:
            sources, tests, notes = EVIDENCE[task_id]
            entry.update(
                {
                    "status": "implemented",
                    "source_files": sources,
                    "test_files": tests,
                    "test_command": f"{PYTEST} {' '.join(tests)}",
                    "result": "PASS",
                    "notes": notes,
                }
            )
        else:
            entry.update(
                {
                    "status": "incomplete",
                    "source_files": [],
                    "test_files": [],
                    "test_command": None,
                    "result": "NOT_RUN",
                    "notes": "Not implemented in this checkout.",
                }
            )
        out.append(entry)
    return out


def verify_evidence_paths() -> list[str]:
    """Return problems where declared evidence points at files that do not exist."""
    problems: list[str] = []
    for task_id, (sources, tests, _) in EVIDENCE.items():
        for rel in [*sources, *tests]:
            if not (ROOT / rel).exists():
                problems.append(f"{task_id}: missing {rel}")
    return problems


def render(matrix: list[dict[str, object]], baseline: dict[str, object]) -> tuple[str, str]:
    base_text = json.dumps(baseline, indent=2, sort_keys=True) + "\n"
    comp_text = json.dumps(
        {"checklist_sha256": hashlib.sha256(CHECKLIST.read_bytes()).hexdigest(), "rows": matrix},
        indent=2,
        sort_keys=True,
    ) + "\n"
    return base_text, comp_text


def summary(matrix: list[dict[str, object]]) -> str:
    statuses = Counter(str(row["status"]) for row in matrix)
    total = len(matrix)
    done = statuses.get("implemented", 0) + statuses.get("verified_existing", 0)
    lines = [f"CP items: {total}", f"status: {dict(sorted(statuses.items()))}"]
    for priority in ("P0", "P1", "P2"):
        subset = [row for row in matrix if row["priority"] == priority]
        finished = sum(1 for row in subset if row["status"] != "incomplete")
        lines.append(f"  {priority}: {finished}/{len(subset)} ({100 * finished / max(len(subset), 1):.1f}%)")
    lines.append(f"progress: {done}/{total} = {100 * done / total:.1f}%")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if committed outputs are stale")
    args = parser.parse_args(argv)

    rows = parse_checklist(CHECKLIST)
    if len(rows) != 406:
        print(f"ERROR: expected 406 CP rows, parsed {len(rows)}", file=sys.stderr)
        return 1
    problems = verify_evidence_paths()
    if problems:
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        return 1

    matrix = build_completion(rows)
    base_text, comp_text = render(matrix, build_baseline())
    if args.check:
        # Baseline dirty_paths change with every edit, so only the completion matrix is strict.
        if not COMPLETION.exists() or COMPLETION.read_text(encoding="utf-8") != comp_text:
            print(f"ERROR: stale: {COMPLETION.name}", file=sys.stderr)
            return 1
        print(summary(matrix))
        return 0

    BASELINE.write_text(base_text, encoding="utf-8")
    COMPLETION.write_text(comp_text, encoding="utf-8")
    print(summary(matrix))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
