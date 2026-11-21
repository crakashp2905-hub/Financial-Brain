"""Generate or verify the tracker-derived F13 forward-edge capability matrix."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from financial_brain.forward_edge.tracker import render_capability_matrix


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/forward-edge/capability-matrix.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the committed matrix is stale")
    args = parser.parse_args(argv)
    expected = render_capability_matrix()
    if args.check:
        try:
            actual = OUTPUT.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"capability matrix unavailable: {exc}", file=sys.stderr)
            return 1
        if actual != expected:
            print("forward-edge capability matrix is stale; run python scripts/forward_edge_capability_matrix.py",
                  file=sys.stderr)
            return 1
        print("Forward-edge capability matrix matches the tracker")
        return 0
    render_capability_matrix(OUTPUT)
    print(f"Wrote {OUTPUT.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
