from datetime import date
from pathlib import Path

from financial_brain import preflight as PF

DAY = date(2026, 11, 4)


def _run(**kw):
    base = dict(resolve=lambda h: True, credentials=lambda: ("k", "t"), free_gb=lambda p: 500.0,
                contracts=lambda db: [{"table": "eod_prices", "ok": True, "why": ""}], db=Path("data/x.duckdb"))
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
