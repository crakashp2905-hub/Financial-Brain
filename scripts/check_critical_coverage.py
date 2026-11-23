"""Fail CI when a named critical module falls below its reviewed line-coverage floor."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Floors were set below measured coverage on the current full suite. The individual
# limits prevent a high-coverage module from masking a weak risk or learning service.
FLOORS: dict[str, float] = {
    "decisions/net_alpha.py": 90,
    "evaluation/champion.py": 80,
    "evaluation/learners.py": 90,
    "evaluation/learned_model.py": 80,
    "evaluation/prospective.py": 80,
    "execution/oms/broker.py": 90,
    "execution/oms/engine.py": 75,
    "execution/oms/evidence.py": 95,
    "execution/oms/journal.py": 90,
    "execution/oms/killswitch.py": 80,
    "execution/oms/kite_broker.py": 90,
    "execution/oms/model.py": 80,
    "execution/oms/quality.py": 90,
    "execution/oms/reconcile.py": 95,
    "execution/oms/risk_gate.py": 80,
    "execution/oms/rollout.py": 85,
    "execution/oms/shortfall.py": 70,
    "execution/oms/slicer.py": 80,
    "interfaces/gateway.py": 75,
    "interfaces/mcp_server.py": 95,
    "interfaces/snapshot.py": 90,
    "interfaces/trader_core.py": 70,
    "opportunity/learning.py": 90,
    "risk/allocator.py": 90,
    "risk/covariance.py": 75,
    "risk/decompose.py": 85,
    "risk/degradation.py": 90,
    "risk/exposure.py": 90,
    "risk/health.py": 90,
    "risk/killswitch.py": 95,
    "risk/limits.py": 80,
    "risk/linalg.py": 90,
    "risk/sizing.py": 90,
    "risk/stress.py": 90,
}


def check(report: dict) -> list[str]:
    files = report.get("files")
    if not isinstance(files, dict):
        return ["coverage JSON must contain a files object"]
    normalized = {str(path).replace("\\", "/").lstrip("./"): record
                  for path, record in files.items()}
    errors: list[str] = []
    for suffix, floor in FLOORS.items():
        matches = [(path, row) for path, row in normalized.items()
                   if path.endswith("src/financial_brain/" + suffix)]
        if len(matches) != 1:
            errors.append(f"{suffix}: expected one coverage record, found {len(matches)}")
            continue
        actual = matches[0][1].get("summary", {}).get("percent_covered")
        if not isinstance(actual, (int, float)):
            errors.append(f"{suffix}: coverage percentage is missing")
        elif actual < floor:
            missing = matches[0][1].get("missing_lines", [])
            detail = f"; uncovered lines: {missing}" if isinstance(missing, list) and missing else ""
            errors.append(f"{suffix}: {actual:.1f}% is below the {floor:.1f}% floor{detail}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="coverage.py JSON report")
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"cannot read coverage report: {exc}", file=sys.stderr)
        return 2
    errors = check(data)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"critical service coverage floors passed for {len(FLOORS)} modules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
