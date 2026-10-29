"""Versioned deterministic guardrail evaluation for the read-only coordinator.

This is a process regression fixture, not a financial-fact benchmark or evidence of
trading utility. Results are read-only and cannot promote a strategy.
"""
from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

from . import brain

CASES = Path(__file__).with_name("eval_cases.v1.json")


def evaluate(path: Path = CASES) -> dict:
    suite = json.loads(path.read_text(encoding="utf-8"))
    today = date.fromisoformat(suite["today"])
    base = suite["base"]
    rows = []
    for case in suite["cases"]:
        item = {**base, **case.get("item", {})}
        state = case.get("kill_state", {"level": "OK"})
        start = time.perf_counter()
        result = brain.decide(item, state, today=today)
        elapsed_ms = (time.perf_counter() - start) * 1000
        passed = result["verdict"] == case["expected"] and result["execution_allowed"] is False
        rows.append({"id": case["id"], "expected": case["expected"],
                     "actual": result["verdict"], "passed": passed,
                     "latency_ms": round(elapsed_ms, 3)})
    return {"suite": suite["version"], "cases": len(rows),
            "passed": sum(row["passed"] for row in rows),
            "failed": sum(not row["passed"] for row in rows),
            "max_latency_ms": max((r["latency_ms"] for r in rows), default=None),
            "results": rows,
            "limits": "Synthetic guardrails only; no financial-fact accuracy, prompt-model, or alpha claim."}
