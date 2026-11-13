"""Project durable forward-runner and PaperAccount journals into executable cohorts.

This adapter preserves missingness: it does not infer sessions, corporate-action
checks, orders, fills, fee facts, or marks. Promotion still requires prospective
cohorts and an independently reviewed source calendar.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Sequence

import pandas as pd

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.evaluation.champion import resolve_executable


class PaperCohortExportError(ValueError):
    """Durable paper records cannot be safely normalized to cohort evidence."""


def build_executable_inputs(*, runner_journal_path: Path, account_journal_path: Path,
                            exchange_sessions: Sequence[str | date],
                            corporate_actions_checked: dict[tuple[str, str], bool]
                            ) -> dict[str, pd.DataFrame]:
    """Build frozen resolver inputs from durable forward and account journals.

    ``corporate_actions_checked`` is explicit caller evidence keyed by (cohort_id,
    YYYY-MM-DD). Missing entries remain false. A forward session's ``cohort_id`` and
    strategy digest come from its immutable SESSION_PLAN; order/fill/fee/mark facts
    come from the account journal and matching runner receipts.
    """
    runner = DurableJournal(Path(runner_journal_path)).records()
    account = DurableJournal(Path(account_journal_path)).records()
    sessions = [date.fromisoformat(str(value)).isoformat() for value in exchange_sessions]
    if not sessions or sessions != sorted(set(sessions)):
        raise PaperCohortExportError("exchange session calendar must be nonempty, unique, and ordered")

    by_session: dict[str, list[dict]] = {}
    for row in runner:
        sid = row.get("session_id")
        if isinstance(sid, str):
            by_session.setdefault(sid, []).append(row)

    plans: dict[str, dict] = {}
    closed: dict[str, dict] = {}
    decision_times: dict[str, str] = {}
    intent_sessions: dict[str, str] = {}
    receipts: dict[str, dict] = {}
    for sid, rows in by_session.items():
        plan = next((r for r in rows if r.get("t") == "SESSION_PLAN"), None)
        if plan is None or not isinstance(plan.get("config"), dict):
            continue
        config = plan["config"]
        if config.get("mode") != "LIVE_PAPER":
            continue
        plans[sid] = config
        end = next((r for r in reversed(rows) if r.get("t") == "SESSION_CLOSED"), None)
        if end is not None and isinstance(end.get("result"), dict):
            closed[sid] = end["result"]
        decision = next((r for r in rows if r.get("t") == "DECISION"), None)
        if decision is not None and isinstance(decision.get("decision_at_utc"), str):
            decision_times[sid] = decision["decision_at_utc"]
        for row in rows:
            if row.get("t") == "PAPER_INTENT" and isinstance(row.get("intent_id"), str):
                intent_sessions[row["intent_id"]] = sid
            elif row.get("t") == "PAPER_EXECUTION" and isinstance(row.get("execution"), dict):
                receipts[str(row.get("intent_id"))] = row["execution"]

    intents = {str(r["client_id"]): r for r in account if r.get("t") == "INTENT"}
    state_by_order: dict[str, str] = {}
    for row in account:
        if row.get("t") == "STATE":
            state_by_order[str(row.get("client_id"))] = str(row.get("state", ""))
    account_fills = [r for r in account if r.get("t") == "FILL"]
    account_fills_by_id = {str(r.get("fill_id")): r for r in account_fills}
    if len(account_fills_by_id) != len(account_fills):
        raise PaperCohortExportError("account journal contains duplicate fill ids")

    scheduled_by_cohort: dict[str, dict] = {}
    evidence_rows: list[dict] = []
    for sid, config in plans.items():
        cid, day = str(config.get("cohort_id", "")), str(config.get("exchange_date", ""))
        strategy = str(config.get("strategy_sha256", ""))
        if not cid or not strategy or day not in sessions:
            raise PaperCohortExportError("forward session plan lacks frozen cohort, strategy, or calendar identity")
        row = {"cohort_id": cid, "day": day, "strategy": strategy,
               "capital": float(config["capital_inr"])}
        prior = scheduled_by_cohort.get(cid)
        if prior is None or day < prior["day"]:
            scheduled_by_cohort[cid] = row
        elif prior["strategy"] != strategy or prior["capital"] != row["capital"]:
            raise PaperCohortExportError("cohort strategy and capital must stay frozen across sessions")
        result = closed.get(sid, {})
        complete = result.get("status") in {"PAPER_COMPLETE", "NO_TRADE", "REJECTED"}
        ca_checked = corporate_actions_checked.get((cid, day), False) is True
        evidence_rows.append({"cohort_id": cid, "day": day, "complete": complete,
                              "corporate_actions_checked": ca_checked})

    order_rows: list[dict] = []
    fill_rows: list[dict] = []
    for intent_id, sid in intent_sessions.items():
        intent = intents.get(intent_id)
        if intent is None:
            raise PaperCohortExportError("forward paper intent has no matching account OMS intent")
        config = plans[sid]
        submitted_at = decision_times.get(sid)
        if not submitted_at:
            raise PaperCohortExportError("paper order has no immutable decision timestamp")
        strategy = str(config["strategy_sha256"])
        day = str(config["exchange_date"])
        status = state_by_order.get(intent_id, "PARTIAL").upper()
        status = {"CANCELLED": "CANCELED"}.get(status, status)
        order_rows.append({"cohort_id": str(config["cohort_id"]), "order_id": intent_id,
                           "strategy": strategy, "day": day, "submitted_at_utc": submitted_at,
                           "symbol": intent["symbol"], "side": intent["side"],
                           "requested_quantity": int(intent["quantity"]), "status": status})
        receipt = receipts.get(intent_id)
        receipt_fills = receipt.get("fills", []) if isinstance(receipt, dict) else []
        for fill in receipt_fills:
            fill_id = str(fill.get("fill_id", ""))
            fact = account_fills_by_id.get(fill_id)
            if fact is None:
                raise PaperCohortExportError("runner receipt fill has no matching durable PaperAccount fill")
            filled_at = fill.get("quote_available_utc") or fill.get("filled_at_utc")
            if not isinstance(filled_at, str):
                raise PaperCohortExportError("fill lacks an observed UTC timestamp; refusing to infer it")
            fill_rows.append({"order_id": intent_id, "fill_id": fill_id,
                              "day": str(fact.get("filled_on") or day), "filled_at_utc": filled_at,
                              "quantity": int(fact["quantity"]), "price": float(fact["price"]),
                              "fees": float(fact.get("fee_amount", "0"))})

    mark_rows = [{"day": str(r["observed_at"])[:10], "symbol": str(r["symbol"]),
                  "price": float(r["price"]), "source": "paper_account_mark",
                  "observed_at_utc": str(r["observed_at"])}
                 for r in account if r.get("t") == "MARK"]
    return {
        "scheduled": pd.DataFrame(scheduled_by_cohort.values(),
                                  columns=["cohort_id", "day", "strategy", "capital"]),
        "session_evidence": pd.DataFrame(evidence_rows,
                                         columns=["cohort_id", "day", "complete", "corporate_actions_checked"]),
        "orders": pd.DataFrame(order_rows, columns=["cohort_id", "order_id", "strategy", "day",
                                                    "submitted_at_utc", "symbol", "side",
                                                    "requested_quantity", "status"]),
        "fills": pd.DataFrame(fill_rows, columns=["order_id", "fill_id", "day", "filled_at_utc",
                                                  "quantity", "price", "fees"]),
        "marks": pd.DataFrame(mark_rows, columns=["day", "symbol", "price", "source", "observed_at_utc"]),
    }


def resolve_from_paper_journals(ledger_con, *, runner_journal_path: Path,
                                account_journal_path: Path,
                                exchange_sessions: Sequence[str | date],
                                corporate_actions_checked: dict[tuple[str, str], bool],
                                horizon: int = 20) -> int:
    """Normalize durable paper evidence and pass it through the executable resolver."""
    inputs = build_executable_inputs(
        runner_journal_path=runner_journal_path, account_journal_path=account_journal_path,
        exchange_sessions=exchange_sessions, corporate_actions_checked=corporate_actions_checked,
    )
    return resolve_executable(ledger_con, exchange_sessions=exchange_sessions,
                              horizon=horizon, **inputs)
