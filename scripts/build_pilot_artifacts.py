"""Generate ship-gate artifacts for the paper pilot from the kernel (blueprint section 6 subset).

Writes, under ``artifacts/``:
  replay/trade-trace.jsonl      one winning paper trade, stage by stage (synthetic tape)
  accounting/reconciliation.json  ledger trial balance and cash == net P&L for win and loss tapes
  capabilities.json              capability summary derived from docs/audits/unified-platform-tracker.json

Every artifact carries schema_version, code_sha, generated_at, environment, fixture_sha256, status and blockers.
Synthetic evidence is labelled; no artifact claims real-market performance or independent verification.
"""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from financial_brain.kernel.pilot import cash_equals_net_pnl, run_pilot, synthetic_tape  # noqa: E402

SCHEMA = 1
ART = ROOT / "artifacts"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _code_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "UNAVAILABLE"


def _header(fixture_text: str, status: str, blockers: list[str]) -> dict:
    return {
        "schema_version": SCHEMA,
        "code_sha": _code_sha(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": {"python": platform.python_version(), "os": platform.system()},
        "fixture_sha256": _sha(fixture_text),
        "status": status,
        "blockers": blockers,
        "synthetic_only": True,
    }


def _find(drift: float, want: str) -> tuple[int, object]:
    for seed in range(1, 200):
        bars = synthetic_tape(seed, drift=drift)
        res = run_pilot(bars)
        if res.outcome == want:
            return seed, res
    raise SystemExit(f"no seed produced {want}")


def main() -> int:
    ART.mkdir(exist_ok=True)
    (ART / "replay").mkdir(exist_ok=True)
    (ART / "accounting").mkdir(exist_ok=True)

    seed, win = _find(0.0009, "WIN")
    trace_text = win.jsonl()
    (ART / "replay" / "trade-trace.jsonl").write_text(trace_text, encoding="utf-8", newline="\n")

    loss_seed, loss = _find(-0.0009, "LOSS")
    checks = []
    for name, res, s in (("win", win, seed), ("loss", loss, loss_seed)):
        checks.append({
            "tape": name,
            "seed": s,
            "trial_balance_ok": res.ledger.trial_balance_ok(),
            "cash_equals_net_pnl": cash_equals_net_pnl(res),
            "net_pnl": str(res.pnl),
            "outcome": res.outcome,
        })
    passed = all(c["trial_balance_ok"] and c["cash_equals_net_pnl"] for c in checks)
    recon = {
        **_header(json.dumps(checks, sort_keys=True), "PASS" if passed else "FAIL", []),
        "checks": checks,
        "note": "Synthetic tapes only; reconciles the paper ledger, not broker or market statements.",
    }
    (ART / "accounting" / "reconciliation.json").write_text(json.dumps(recon, indent=2) + "\n", encoding="utf-8")

    tracker = json.loads((ROOT / "docs" / "audits" / "unified-platform-tracker.json").read_text(encoding="utf-8"))
    rows = tracker["tasks"]
    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    capabilities = {
        **_header(json.dumps(by_status, sort_keys=True), "PARTIAL", [
            "most FB tasks are open; see tracker",
            "no real market data, no broker, no forward evidence",
        ]),
        "task_counts": by_status,
        "total_tasks": len(rows),
        "implemented_tasks": [r["id"] for r in rows if r["status"] == "implemented"],
    }
    (ART / "capabilities.json").write_text(json.dumps(capabilities, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"trace_lines": trace_text.count("\n"), "reconciliation": recon["status"],
                      "net_pnl_win": str(win.pnl), "net_pnl_loss": str(loss.pnl), "decimal_check": str(Decimal(0))},
                     indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
