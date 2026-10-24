"""Read-only coverage audit for locally recorded inverse-policy evidence."""
from __future__ import annotations

from .inverse_strategy import ACTIONS


def audit_existing_history(con) -> dict:
    actions = dict(con.execute("SELECT action, COUNT(*) FROM decisions GROUP BY action").fetchall())
    dispositions = dict(con.execute("SELECT disposition, COUNT(*) FROM opportunity_memory GROUP BY disposition").fetchall())
    explicit = sum(dispositions.get(name, 0) for name in ("TAKEN", "HELD", "EXITED"))
    rejected = sum(value for name, value in dispositions.items() if name.startswith("REJECTED_"))
    return {"decision_rows": sum(actions.values()), "decision_actions": actions,
            "recognized_decision_actions": sum(actions.get(name, 0) for name in ACTIONS),
            "opportunity_dispositions": dispositions,
            "explicit_paper_action_rows": explicit, "rejected_candidate_rows": rejected,
            "complete_asof_feature_snapshots": "not established by these tables",
            "training_ready": False,
            "reasons": ["WATCH/AVOID are not silently recoded as BUY/SELL/HOLD.",
                        "Rejected candidates are not observed orders or HOLD actions.",
                        "Paper dispositions lack the complete contemporaneous feature and portfolio state.",
                        "A point-in-time snapshot join and independent outcomes are required."]}
