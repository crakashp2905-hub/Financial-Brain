"""Mutation checks for the chart-pattern package (CP-0151).

Each mutant changes one comparison or guard in a pattern module, runs the tests that should notice, and records
whether the change was killed (a test failed) or survived (every test still passed). Files are restored byte-for-byte
after each run. Survivors are reported, not hidden.

Run from the repository root:

    python scripts/mutation_patterns.py

Writes ``docs/audits/mutation-report.json`` and exits non-zero if any mutant survives.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = ROOT / "src" / "financial_brain" / "patterns"
REPORT = ROOT / "docs" / "audits" / "mutation-report.json"


@dataclass(frozen=True)
class Mutant:
    name: str
    path: Path
    old: bytes
    new: bytes
    tests: tuple[str, ...]


MUTANTS: tuple[Mutant, ...] = (
    Mutant("pivot high: strict left becomes non-strict", PATTERNS / "pivots.py",
           b"highs[left] > highs[j] for j in lo_window", b"highs[left] >= highs[j] for j in lo_window",
           ("tests/test_pattern_pivots.py",)),
    Mutant("pivot low: strict left becomes non-strict", PATTERNS / "pivots.py",
           b"lows[left] < lows[j] for j in lo_window", b"lows[left] <= lows[j] for j in lo_window",
           ("tests/test_pattern_pivots.py",)),
    Mutant("breakout: close equal to the level now confirms", PATTERNS / "detectors.py",
           b"if upper is not None and c > upper:", b"if upper is not None and c >= upper:",
           ("tests/test_pattern_detectors.py", "tests/test_pattern_wave9_detectors.py", "tests/test_pattern_wave9_final.py")),
    Mutant("failure: close equal to the level now fails", PATTERNS / "detectors.py",
           b"if fail_down is not None and c < fail_down:", b"if fail_down is not None and c <= fail_down:",
           ("tests/test_pattern_detectors.py", "tests/test_pattern_wave9_detectors.py", "tests/test_pattern_wave9_final.py")),
    Mutant("causality: triangles emit before their last pivot is known", PATTERNS / "detectors.py",
           b"if known != e:", b"if False:",
           ("tests/test_pattern_detectors.py",)),
    Mutant("causality: cup and handle drops the pivot-known guard", PATTERNS / "detectors2.py",
           b"if e >= n or r.known_at_index > e:", b"if e >= n:",
           ("tests/test_pattern_wave9_detectors.py", "tests/test_pattern_wave9_final.py")),
    Mutant("time: bar completing exactly at the query time is excluded", PATTERNS / "marketdata.py",
           b"if completed <= cutoff:", b"if completed < cutoff:",
           ("tests/test_pattern_wave9_realism.py",)),
    Mutant("calendar: a session box accepts reversed dates", PATTERNS / "marketdata.py",
           b"if end < start or max_sessions < 1:", b"if max_sessions < 1:",
           ("tests/test_pattern_wave9_realism.py",)),
)


def _run(tests: tuple[str, ...]) -> int:
    cmd = [sys.executable, "-m", "pytest", "-q", "-x", *tests]
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, timeout=600, check=False).returncode


def main() -> int:
    results: list[dict[str, object]] = []
    survivors = 0
    for m in MUTANTS:
        original = m.path.read_bytes()
        if original.count(m.old) < 1:
            raise SystemExit(f"mutant anchor not found: {m.name}")
        mutated = original.replace(m.old, m.new, 1)
        try:
            m.path.write_bytes(mutated)
            code = _run(m.tests)
        finally:
            m.path.write_bytes(original)
        killed = code != 0
        survivors += 0 if killed else 1
        results.append({"mutant": m.name, "file": str(m.path.relative_to(ROOT)).replace("\\", "/"),
                        "tests": list(m.tests), "status": "KILLED" if killed else "SURVIVED"})
        print(f"{'KILLED  ' if killed else 'SURVIVED'} {m.name}")
    REPORT.write_text(json.dumps({"mutants": results, "killed": len(results) - survivors,
                                  "survived": survivors}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
