"""The incident ledger: every lost session has a cause, every cause a guard, and an unscheduled guard is not a guard."""
from __future__ import annotations

import pandas as pd

from financial_brain.learning import incidents as IN

NO_TOKEN = "quotes failing: no Kite access token for today - run the login flow (tokens expire each morning around 6am IST)"
NET = "quotes failing: /quote: <urlopen error [Errno 11001] getaddrinfo failed>"


def _runs():
    rows = [("2026-10-06", "live", "killed", ""), ("2026-10-07", "live", "complete", '{"skipped": 1}'),
            ("2026-10-12", "live", "aborted", NO_TOKEN), ("2026-10-13", "live", "aborted", NO_TOKEN),
            ("2026-10-14", "live", "complete", ""), ("2026-11-03", "live", "aborted", NET),
            ("2026-11-04", "live", "complete", ""), ("2026-10-12", "replay", "complete", "")]
    return pd.DataFrame(rows, columns=["day", "mode", "status", "note"])


SESSIONS = ["2026-10-06", "2026-10-07", "2026-10-08", "2026-10-12", "2026-10-13", "2026-10-14", "2026-11-03", "2026-11-04"]


def test_causes_come_from_the_recorded_text():
    assert IN.classify_text(NO_TOKEN) == "NO_LOGIN" and IN.classify_text(NET) == "NETWORK"
    assert IN.classify_text("IO Error: Could not set lock on file") == "DB_LOCKED"
    assert IN.classify_text("6 days behind (max 4)") == "DATA_STALE"
    assert IN.classify_text("something unexpected") == "CRASH"


def test_paper_runs_give_one_incident_per_lost_session_including_the_one_never_started():
    rows = IN.from_paper_runs(_runs(), SESSIONS)
    by = {(r["day"], r["cause"]) for r in rows}
    assert by == {("2026-10-06", "CONSOLE_KILLED"), ("2026-10-08", "NO_RUN"), ("2026-10-12", "NO_LOGIN"),
                  ("2026-10-13", "NO_LOGIN"), ("2026-11-03", "NETWORK")}
    assert all(r["severity"] == IN.SESSION_LOST for r in rows)


def test_replay_runs_are_never_counted_as_live_sessions():
    only_replay = pd.DataFrame([("2026-10-12", "replay", "complete", "")], columns=["day", "mode", "status", "note"])
    rows = IN.from_paper_runs(only_replay, ["2026-10-12"])
    assert [r["cause"] for r in rows] == ["NO_RUN"]


def test_refresh_log_tracebacks_become_degraded_incidents(tmp_path):
    (tmp_path / "refresh-2026-11-02.log").write_text(
        "ok\nTraceback (most recent call last):\n  File \"x.py\", line 1, in <module>\n    boom()\n"
        "duckdb.IOException: IO Error: Could not set lock on file \"d.duckdb\"\nnext step\n"
        "Traceback (most recent call last):\n  File \"y.py\", line 2\nurllib.error.URLError: <urlopen error timed out>\n",
        encoding="utf-8")
    rows = IN.from_refresh_logs(tmp_path)
    assert [(r["cause"], r["severity"]) for r in rows] == [("DB_LOCKED", IN.DEGRADED), ("NETWORK", IN.DEGRADED)]
    assert all(r["day"] == "2026-11-02" and r["source"] == "nightly_refresh" for r in rows)


def test_preflight_failures_are_recorded_as_caught(tmp_path):
    (tmp_path / "preflight-2026-11-05.log").write_text(
        "OK   network    all hosts resolve\nFAIL kite       no Kite access token for today\n"
        "FAIL depth_task the 09:10 depth archive task is not registered\n", encoding="utf-8")
    rows = IN.from_preflight_logs(tmp_path)
    assert {(r["cause"], r["severity"], r["detected_by"]) for r in rows} == {
        ("NO_LOGIN", IN.CAUGHT, "preflight"), ("UNSCHEDULED_COLLECTOR", IN.CAUGHT, "preflight")}


def test_depth_archive_runs_that_failed_are_incidents():
    runs = pd.DataFrame([("2026-11-12", "no_login", "no Kite login for the day by 10:30"), ("2026-11-13", "complete", "")],
                        columns=["day", "status", "note"])
    rows = IN.from_depth_runs(runs)
    assert [(r["cause"], r["source"]) for r in rows] == [("NO_LOGIN", "depth_archive")]


def test_the_report_separates_before_and_after_the_guard_and_flags_an_unscheduled_one():
    inc = pd.DataFrame(IN.from_paper_runs(_runs(), SESSIONS))
    rep = IN.report(inc, sessions=SESSIONS, today="2026-11-12", task_registered=lambda name: False)
    a = rep["availability"]
    assert a["sessions"] == 8 and a["lost"] == 5 and a["completed"] == 3
    by = {c["cause"]: c for c in rep["causes"]}
    nl = by["NO_LOGIN"]
    assert nl["sessions_lost"] == 2 and nl["lost_before_guard"] == 2 and nl["lost_after_guard"] == 0
    assert nl["state"] == "GUARD NOT ACTIVE" and "Preflight" in nl["task"]
    assert by["CONSOLE_KILLED"]["state"] == "GUARDED"                  # always-on guard: no task needed
    assert by["NO_RUN"]["state"] == "GUARD NOT ACTIVE" and "Daily Refresh" in by["NO_RUN"]["task"]    # a detective guard, on the nightly task
    assert {c["cause"] for c in IN.open_lessons(rep)} == {"NO_LOGIN", "NETWORK", "NO_RUN"}


def test_once_the_task_is_registered_the_guard_is_active_and_recurrences_after_it_are_counted():
    runs = pd.concat([_runs(), pd.DataFrame([("2026-11-20", "live", "aborted", NO_TOKEN)], columns=["day", "mode", "status", "note"])])
    inc = pd.DataFrame(IN.from_paper_runs(runs, SESSIONS + ["2026-11-20"]))
    rep = IN.report(inc, sessions=SESSIONS + ["2026-11-20"], today="2026-11-21", task_registered=lambda name: True)
    nl = {c["cause"]: c for c in rep["causes"]}["NO_LOGIN"]
    assert nl["state"] == "GUARDED" and nl["lost_before_guard"] == 2 and nl["lost_after_guard"] == 1
    assert IN.open_lessons(rep) == []                                   # every cause that cost a session now has an active control
    assert "GUARDED" in IN.render(rep)


def test_a_guard_that_does_not_exist_yet_is_not_credited():
    st = IN.guard_status("UNSCHEDULED_COLLECTOR", today="2026-11-01", task_registered=lambda n: True)
    assert st["state"] == "UNGUARDED"


def test_incidents_are_stored_once_however_often_the_collector_runs():
    from financial_brain.learning.store import Store
    rows = IN.from_paper_runs(_runs(), SESSIONS)
    with Store(":memory:") as st:
        assert st.add_incidents(rows) == len(rows)
        assert st.add_incidents(rows) == 0
        assert len(st.incidents()) == len(rows)


def test_a_cause_with_no_control_at_all_stays_an_open_lesson():
    inc = pd.DataFrame([IN._row("2026-11-02", "paper_runner", "CRASH", IN.SESSION_LOST, "boom")])
    rep = IN.report(inc, sessions=["2026-11-02"], today="2026-11-12", task_registered=lambda n: True)
    assert [c["cause"] for c in IN.open_lessons(rep)] == ["CRASH"] and rep["causes"][0]["state"] == "UNGUARDED"
