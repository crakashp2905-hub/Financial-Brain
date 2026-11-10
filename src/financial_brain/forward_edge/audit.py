"""One-command evidence audit (FE-0010): red / yellow / green with exact blocking reasons.

Overall status is the worst check. A check is never green when the evidence it guards is missing: no independent
custody root means RED for formal claims, because LOCAL_ONLY is the only status available. Run with::

    python -m financial_brain.forward_edge.audit [--repo PATH] [--json]

Exit status: 0 when GREEN, 1 otherwise. The audit reads and never writes.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from financial_brain.forward_edge import modes, trials
from financial_brain.forward_edge.modes import Mode, ModeError

RANK = {"GREEN": 0, "YELLOW": 1, "RED": 2}


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    reason: str


def _pins(root: Path) -> Check:
    try:
        problems = trials.verify_pins(root)
    except (OSError, ValueError) as exc:
        return Check("protected_pins", "RED", f"pin file unreadable: {exc}")
    if problems:
        return Check("protected_pins", "RED", "protected records differ from pins: " + "; ".join(problems))
    return Check("protected_pins", "GREEN", "all pinned protected records match their digests")


def _trial_budget(root: Path) -> Check:
    try:
        status = trials.budget_status(root / trials.LEDGER_REL)
    except (OSError, ValueError, KeyError) as exc:
        return Check("trial_budget", "RED", f"trial ledger unreadable: {exc}")
    if status.exhausted:
        return Check("trial_budget", "YELLOW",
                     f"2026Q4 protected budget {status.spent}/{status.budget} spent; new trials need an ADR amendment")
    return Check("trial_budget", "GREEN", f"{status.spent}/{status.budget} protected trials used")


def _custody(root: Path) -> Check:
    if not (root / "docs" / "forward-edge" / "trust-roots.json").is_file():
        return Check("independent_custody", "RED",
                     "INDEPENDENT_TIMESTAMP_RECEIPTS_NOT_CONFIGURED: only LOCAL_ONLY is possible; no formal forward claim")
    return Check("independent_custody", "GREEN", "trust roots pinned; verification required on every receipt")


def _promotion_metric(root: Path) -> Check:
    try:
        from financial_brain.evaluation import champion
    except Exception as exc:  # noqa: BLE001 - audit must report, not crash
        return Check("promotion_metric", "RED", f"champion module unavailable: {exc.__class__.__name__}")
    if not hasattr(champion, "resolve_executable"):
        return Check("promotion_metric", "RED",
                     f"resolver for {champion.EXECUTABLE_METRIC} does not exist; {champion.RANKING_METRIC} is diagnostic only")
    return Check("promotion_metric", "GREEN", f"{champion.EXECUTABLE_METRIC} resolver present")


def _live_cash(now: datetime) -> Check:
    try:
        modes.resolve_mode(Mode.LIVE_CASH, now=now)
    except ModeError:
        return Check("live_cash", "GREEN", "LIVE_CASH refused by default (fail closed)")
    return Check("live_cash", "RED", "LIVE_CASH was granted without approval: fail-closed guard broken")


def _baseline(root: Path) -> Check:
    found = sorted((root / "docs" / "forward-edge").glob("baseline-*.json")) if (root / "docs" / "forward-edge").is_dir() else []
    if not found:
        return Check("baseline", "YELLOW", "no baseline capture committed; run forward_edge.baseline")
    return Check("baseline", "GREEN", f"baseline present: {found[-1].name}")


def run_audit(root: Path, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    root = Path(root)
    checks = [_pins(root), _trial_budget(root), _custody(root), _promotion_metric(root), _live_cash(now),
              _baseline(root)]
    worst = max((c.status for c in checks), key=lambda s: RANK[s])
    return {
        "generated_at_utc": now.astimezone(timezone.utc).isoformat(),
        "overall": worst,
        "formal_forward_claim_allowed": worst == "GREEN",
        "checks": [asdict(c) for c in checks],
        "blocking": [f"{c.name}: {c.reason}" for c in checks if c.status == "RED"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[3]))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = run_audit(Path(args.repo))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"OVERALL {report['overall']}")
        for c in report["checks"]:
            print(f"  [{c['status']}] {c['name']}: {c['reason']}")
    return 0 if report["overall"] == "GREEN" else 1


if __name__ == "__main__":
    sys.exit(main())
