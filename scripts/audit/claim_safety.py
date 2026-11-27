"""Fail CI on unqualified guarantees or manufactured-performance claims in product prose."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GUARANTEED_PROFIT", re.compile(r"\bguarantee(?:d|s)?\b.{0,60}\b(?:profit|profitable|return|win)\w*\b", re.I)),
    ("ZERO_LOSS", re.compile(r"\bzero[ -]+loss(?:es)?\b", re.I)),
    ("ACCURACY_AS_ALPHA", re.compile(
        r"\b(?:high\s+)?accuracy\b.{0,40}\b(?:is|means|equals|proves|=)\b.{0,20}\balpha\b", re.I)),
    ("SYNTHETIC_FORWARD_RESULT", re.compile(
        r"\bsynthetic\b.{0,30}\bforward\b.{0,30}\b(?:result|return|performance|profit)\w*\b", re.I)),
    ("FALLBACK_CLOSE_PNL", re.compile(
        r"\b(?:realized|realised)\b.{0,40}\b(?:return|pnl|profit)\b.{0,50}\bfallback\b.{0,20}\bclose\b", re.I)),
)
DENIAL = re.compile(
    r"\b(?:not|never|no|cannot|can't|won't|mustn't|must not|do not|don't|refus\w*|"
    r"prohibit\w*|forbid\w*|disallow\w*|prevent\w*|reject\w*|ban\w*|without)\b", re.I)


def violations(text: str) -> list[tuple[int, str, str]]:
    """Return line, rule and phrase for claims that lack nearby explicit denial language."""
    found: list[tuple[int, str, str]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        # A refusal regex names prohibited language as data; it is not an output claim.
        if re.search(r"\bre\.compile\s*\(", line):
            continue
        for rule, pattern in RULES:
            for match in pattern.finditer(line):
                if not DENIAL.search(line):
                    found.append((line_number, rule, match.group(0)))
    return found


def scan(root: Path) -> list[str]:
    errors: list[str] = []
    files = [*root.glob("README*.md"), *root.joinpath("docs").rglob("*.md"),
             *root.joinpath("src/financial_brain").rglob("*.py")]
    for path in sorted(set(files)):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(f"{path.relative_to(root).as_posix()}: cannot scan: {exc}")
            continue
        for line, rule, phrase in violations(text):
            errors.append(f"{path.relative_to(root).as_posix()}:{line}: {rule}: {phrase!r}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    errors = scan(args.root.resolve())
    if errors:
        print("Unsupported financial-performance claims found:")
        print("\n".join(errors))
        return 1
    print("Claim-safety scan passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
