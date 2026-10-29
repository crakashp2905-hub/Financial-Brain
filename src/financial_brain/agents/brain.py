"""Fail-closed, read-only coordinator over the trader workstation snapshot.

Specialists report facts and vetoes. The coordinator never turns modelled alpha or an
LLM opinion into an order. RESEARCH_REVIEW means a human may inspect the evidence;
it is not a buy instruction. No broker, network, or model call exists in this module.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..risk import killswitch as KS

DEFAULT_SNAPSHOT = Path("data/terminal/opportunities.json")
MAX_AGE_DAYS = 4  # weekend allowance; not a substitute for an exchange calendar


def _agent(name: str, status: str, reasons: list[str], **facts) -> dict:
    return {"agent": name, "status": status, "reasons": reasons, "facts": facts}


def data_agent(item: dict, today: date) -> dict:
    try:
        asof = date.fromisoformat(str(item["timestamp"])[:10])
    except (KeyError, TypeError, ValueError):
        return _agent("data", "BLOCK", ["missing or invalid opportunity as-of date"])
    age = (today - asof).days
    if age < 0 or age > MAX_AGE_DAYS:
        return _agent("data", "BLOCK", [f"snapshot is {age} calendar days old"], asof=str(asof))
    if not item.get("ticker") or not item.get("instrument"):
        return _agent("data", "BLOCK", ["missing instrument identity"], asof=str(asof))
    return _agent("data", "PASS", [], asof=str(asof), age_days=age)


def evidence_agent(item: dict) -> dict:
    evidence = item.get("evidence") or {}
    status = str(evidence.get("status") or "UNKNOWN").upper()
    ids = evidence.get("ids") or []
    # No evidence label is elevated to a deployable edge by this coordinator.
    if not isinstance(ids, list) or not ids:
        return _agent("evidence", "BLOCK", ["no traceable evidence ids"], tier=status)
    if status not in {"PAPER_FORWARD", "LIVE_FORWARD", "CONFIRMED"}:
        return _agent("evidence", "RESEARCH", ["edge not independently confirmed"], tier=status,
                      evidence_ids=ids)
    return _agent("evidence", "REVIEW", ["forward label requires human verification"],
                  tier=status, evidence_ids=ids)


def risk_agent(item: dict, kill_state: dict) -> dict:
    level = kill_state.get("level")
    if level not in {KS.OK, KS.REDUCE, KS.HALT}:
        return _agent("risk", "BLOCK", ["kill-switch state missing or invalid"])
    if level == KS.HALT:
        return _agent("risk", "BLOCK", ["kill switch is HALT"], level=level)
    size = item.get("position_size") or {}
    stop = (item.get("invalidation") or {}).get("stop")
    try:
        shares = float(size["shares"])
        stop = float(stop)
    except (KeyError, TypeError, ValueError):
        return _agent("risk", "BLOCK", ["position size or invalidation missing"], level=level)
    if not math.isfinite(shares) or not math.isfinite(stop) or shares <= 0 or stop <= 0:
        return _agent("risk", "BLOCK", ["invalid size or stop"], level=level)
    if level == KS.REDUCE:
        return _agent("risk", "CAUTION", ["kill switch requires reduced exposure"], level=level)
    return _agent("risk", "PASS", [], level=level)


def economics_agent(item: dict) -> dict:
    chain = item.get("chain") or {}
    try:
        net = float(chain["expected_net_alpha"])
        portfolio = float(chain["portfolio_adjusted_alpha"])
        cost = float(item["expected_cost"])
    except (KeyError, TypeError, ValueError):
        return _agent("economics", "BLOCK", ["itemized economics missing"])
    if not all(math.isfinite(x) for x in (net, portfolio, cost)) or cost < 0:
        return _agent("economics", "BLOCK", ["invalid economics"])
    if min(net, portfolio) <= 0:
        return _agent("economics", "NO_EDGE", ["modelled alpha does not clear costs and crowding"],
                      net=net, portfolio_adjusted=portfolio, cost=cost)
    return _agent("economics", "MODEL_ONLY", ["positive estimate is not validated alpha"],
                  net=net, portfolio_adjusted=portfolio, cost=cost)


def adversary_agent(item: dict) -> dict:
    reasons = []
    if not (item.get("invalidation") or {}).get("rules"):
        reasons.append("no explicit invalidation rule")
    if not item.get("liquidity"):
        reasons.append("liquidity evidence missing")
    if not item.get("forecast_distribution"):
        reasons.append("forecast distribution missing")
    return _agent("adversary", "BLOCK" if reasons else "PASS", reasons)


def decide(item: dict, kill_state: dict, *, today: date | None = None) -> dict:
    """One deterministic, reproducible research verdict; no approval or execution path."""
    if not isinstance(item, dict):
        item = {}
    today = today or datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
    specialists = [data_agent(item, today), evidence_agent(item), risk_agent(item, kill_state),
                   economics_agent(item), adversary_agent(item)]
    by_name = {s["agent"]: s for s in specialists}
    if any(s["status"] == "BLOCK" for s in specialists):
        verdict = "ABSTAIN"
    elif by_name["economics"]["status"] == "NO_EDGE":
        verdict = "NO_NEW_RISK"
    elif by_name["evidence"]["status"] == "REVIEW" and by_name["risk"]["status"] == "PASS":
        verdict = "HUMAN_REVIEW"
    else:
        verdict = "RESEARCH_WATCH"
    canonical = json.dumps({"item": item, "kill_state": kill_state}, sort_keys=True,
                           separators=(",", ":"), allow_nan=False, default=str)
    return {"schema": 1, "agent": "financial-brain", "symbol": item.get("ticker"),
            "asof": item.get("timestamp"), "verdict": verdict, "specialists": specialists,
            "input_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
            "execution_allowed": False,
            "note": "Research decision only. No order, agent approval, or profit claim."}


def run(snapshot: list[dict], kill_state: dict, *, today: date | None = None,
        limit: int = 10) -> dict:
    if not isinstance(snapshot, list) or not isinstance(kill_state, dict):
        raise ValueError("snapshot must be a list and kill state an object")
    if limit < 1:
        raise ValueError("limit must be positive")
    decisions = [decide(item, kill_state, today=today) for item in snapshot[:limit]]
    return {"schema": 1, "agent": "financial-brain", "decisions": decisions,
            "by_verdict": {v: sum(d["verdict"] == v for d in decisions)
                           for v in ("ABSTAIN", "NO_NEW_RISK", "RESEARCH_WATCH", "HUMAN_REVIEW")},
            "execution_allowed": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Financial-Brain agent coordinator")
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--kill-switch", type=Path, default=KS.DEFAULT_STATE)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(argv)
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    # Missing kill state is not silently treated as OK by an autonomous coordinator.
    kill_state = (json.loads(args.kill_switch.read_text(encoding="utf-8"))
                  if args.kill_switch.exists() else {})
    print(json.dumps(run(snapshot, kill_state, limit=args.limit), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
