from datetime import date
from pathlib import Path

from financial_brain import preflight as PF

DAY = date(2026, 11, 4)


def _run(**kw):
    base = dict(resolve=lambda h: True, credentials=lambda: ("k", "t"), free_gb=lambda p: 500.0,
                contracts=lambda db: [{"table": "eod_prices", "ok": True, "why": ""}], db=Path("data/x.duckdb"),
                depth_task=lambda: True)
    return PF.run(today=DAY, **{**base, **kw})


def test_a_healthy_morning_raises_no_alert():
    r = _run()
    assert all(x["ok"] for x in r)
    assert PF.alerts(r, DAY) == []


def test_missing_login_dns_failure_low_disk_and_stale_data_each_fail_and_alert_once_per_set():
    def no_token():
        raise RuntimeError("no Kite access token for today")

    r = _run(resolve=lambda h: h != "api.kite.trade", credentials=no_token, free_gb=lambda p: 10.0,
             contracts=lambda db: [{"table": "eod_prices", "ok": False, "why": "6 days behind (max 4)"}])
    failed = {x["name"] for x in r if not x["ok"]}
    assert failed == {"network", "kite", "disk", "contracts"}
    (a,) = PF.alerts(r, DAY)
    assert a["key"].startswith("preflight:2026-11-04:") and "api.kite.trade" in a["body"]
    assert "holiday" in a["body"]
    # a different failure set is a different alert, so a second problem is not hidden by the first
    other = PF.alerts(_run(credentials=no_token), DAY)[0]
    assert other["key"] != a["key"]


def test_a_locked_database_is_reported_not_raised():
    def locked(db):
        raise OSError("database is locked by another process")

    r = _run(contracts=locked)
    assert [x for x in r if x["name"] == "database"][0]["ok"] is False
    assert not any(x["name"] == "contracts" for x in r)


def test_an_unregistered_depth_task_has_its_own_alert_and_does_not_look_like_a_session_failure():
    r = _run(depth_task=lambda: False)
    assert [x for x in r if x["name"] == "depth_task"][0]["ok"] is False
    (a,) = PF.alerts(r, DAY)
    assert a["title"] == "Depth archive is not scheduled" and "register_depth_archive_task" in a["body"]
    # both at once: two separate alerts, the session one is unchanged
    both = PF.alerts(_run(depth_task=lambda: False, free_gb=lambda p: 10.0), DAY)
    assert {x["title"] for x in both} == {"Paper session may not run today", "Depth archive is not scheduled"}


def test_the_task_query_never_raises_a_false_alarm_when_it_cannot_ask(monkeypatch):
    import subprocess

    def boom(*a, **k):
        raise OSError("no schtasks")

    monkeypatch.setattr(subprocess, "run", boom)
    assert PF._depth_task_registered() is True
