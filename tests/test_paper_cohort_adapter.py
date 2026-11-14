import duckdb
import pytest

from financial_brain.evaluation.paper_cohort_adapter import (
    PaperCohortExportError,
    build_executable_inputs,
    resolve_from_paper_journals,
)
from financial_brain.forward_edge.durable import DurableJournal


STRATEGY = "a" * 64
COHORT = "prospective-cohort-1"
INTENT = "forward-intent-1"


def _session(session_id, day, *, status, with_order=False):
    config = {"mode": "LIVE_PAPER", "exchange_date": day, "cohort_id": COHORT,
              "strategy_sha256": STRATEGY, "capital_inr": "10000", "session_id": session_id}
    rows = [{"t": "SESSION_PLAN", "session_id": session_id, "config": config},
            {"t": "SESSION_CLOSED", "session_id": session_id, "result": {"status": status}}]
    if with_order:
        rows.extend([
            {"t": "DECISION", "session_id": session_id,
             "decision_at_utc": f"{day}T04:00:00+00:00"},
            {"t": "PAPER_INTENT", "session_id": session_id, "intent_id": INTENT},
            {"t": "PAPER_EXECUTION", "session_id": session_id, "intent_id": INTENT,
             "execution": {"status": "FILLED", "fills": [{"fill_id": "fill-1",
                 "quantity": 2, "price": "100", "quote_available_utc": f"{day}T04:00:01+00:00"}]}}
        ])
    return rows


def _journals(tmp_path, *, include_fill_time=True):
    runner_path, account_path = tmp_path / "runner.jsonl", tmp_path / "account.jsonl"
    runner = DurableJournal(runner_path)
    for row in _session("s1", "2026-10-05", status="PAPER_COMPLETE", with_order=True):
        runner.append(row)
    for row in _session("s2", "2026-10-06", status="NO_TRADE"):
        runner.append(row)
    account = DurableJournal(account_path)
    account_rows = [
        {"t": "INTENT", "client_id": INTENT, "symbol": "NSE:INFY", "side": "BUY",
         "quantity": 2},
        {"t": "STATE", "client_id": INTENT, "state": "FILLED"},
        {"t": "FILL", "client_id": INTENT, "fill_id": "fill-1", "symbol": "NSE:INFY",
         "quantity": 2, "price": "100", "fee_amount": "1", "filled_on": "2026-10-05"},
        {"t": "MARK", "mark_id": "m1", "symbol": "NSE:INFY", "price": "100",
         "observed_at": "2026-10-05T10:00:00+00:00"},
        {"t": "MARK", "mark_id": "m2", "symbol": "NSE:INFY", "price": "110",
         "observed_at": "2026-10-06T10:00:00+00:00"},
    ]
    for row in account_rows:
        account.append(row)
    if not include_fill_time:
        # The normalized receipt is the source of fill time; remove it to test fail-closed behavior.
        records = runner.records()
        changed = DurableJournal(runner_path.with_name("runner-missing-time.jsonl"))
        for row in records:
            if row.get("t") == "PAPER_EXECUTION":
                row["execution"]["fills"][0].pop("quote_available_utc")
            changed.append(row)
        runner_path = changed.path
    return runner_path, account_path


def test_journals_normalize_into_executable_cohort_inputs_and_resolve(tmp_path):
    runner_path, account_path = _journals(tmp_path)
    calendar = ["2026-10-05", "2026-10-06"]
    facts = build_executable_inputs(
        runner_journal_path=runner_path, account_journal_path=account_path,
        exchange_sessions=calendar, corporate_actions_checked={(COHORT, day): True for day in calendar},
    )
    assert facts["scheduled"].iloc[0]["strategy"] == STRATEGY
    assert facts["orders"].iloc[0]["status"] == "FILLED"
    assert facts["fills"].iloc[0]["fees"] == 1.0
    assert facts["session_evidence"]["complete"].all()

    con = duckdb.connect()
    assert resolve_from_paper_journals(
        con, runner_journal_path=runner_path, account_journal_path=account_path,
        exchange_sessions=calendar,
        corporate_actions_checked={(COHORT, day): True for day in calendar}, horizon=1,
    ) == 1
    row = con.execute("SELECT gross, cost, net, complete FROM champion_executable_cohorts").fetchone()
    assert row == pytest.approx((0.002, 0.0001, 0.0019, True))
    con.close()


def test_missing_corporate_action_attestation_is_not_inferred(tmp_path):
    runner_path, account_path = _journals(tmp_path)
    facts = build_executable_inputs(
        runner_journal_path=runner_path, account_journal_path=account_path,
        exchange_sessions=["2026-10-05", "2026-10-06"], corporate_actions_checked={},
    )
    assert not facts["session_evidence"]["corporate_actions_checked"].any()


def test_missing_fill_timestamp_refuses_executable_export(tmp_path):
    runner_path, account_path = _journals(tmp_path, include_fill_time=False)
    with pytest.raises(PaperCohortExportError, match="observed UTC timestamp"):
        build_executable_inputs(
            runner_journal_path=runner_path, account_journal_path=account_path,
            exchange_sessions=["2026-10-05", "2026-10-06"],
            corporate_actions_checked={(COHORT, "2026-10-05"): True},
        )
