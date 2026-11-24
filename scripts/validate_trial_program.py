"""Validate explicit, fail-closed protected-trial windows and budgets."""
from pathlib import Path
from datetime import date
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from financial_brain.forward_edge.trials import (
    LEDGER_REL,
    load_programs,
    verify_pins,
    budget_status,
)


if __name__ == "__main__":
    root = ROOT
    programs = load_programs(root)
    q1 = next(program for program in programs if program["program_id"] == "Q1-2027")
    if q1["status"] != "NOT_AUTHORIZED" or q1["budget"] is not None:
        raise SystemExit("Q1-2027 must stay closed until owner authorization and a positive budget are recorded")
    q4 = next(program for program in programs if program["program_id"] == "Q4-2026")
    q4_status = budget_status(
        root / LEDGER_REL,
        (date.fromisoformat(q4["start_date"]), date.fromisoformat(q4["end_date"])),
        q4["budget"],
    )
    if q4["status"] != "EXHAUSTED" or not q4_status.exhausted:
        raise SystemExit("Q4-2026 must remain marked exhausted while all 13 protected trials are spent")
    pin_errors = verify_pins(root)
    if pin_errors:
        raise SystemExit("protected trial records/program changed without a pin update: " + "; ".join(pin_errors))
    print(f"validated {len(programs)} protected-trial windows; Q1-2027 remains unauthorized")
