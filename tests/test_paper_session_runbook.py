import json

from financial_brain.forward_edge import cli


def test_documented_offline_premarket_session_close_and_report_sequence(capsys):
    assert cli.main(["preflight"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True

    assert cli.main(["preflight", "--fail", "feeds"]) == 2
    failed = json.loads(capsys.readouterr().out)
    assert failed["ok"] is False and "feeds" in failed["failures"]

    assert cli.main(["paper-run", "--fixture", "win"]) == 0
    simulated = json.loads(capsys.readouterr().out)
    assert simulated["mode"] == "FIXTURE_REPLAY"
    assert simulated["physical_orders"] is False
    assert simulated["broker"] == "NONE"

    assert cli.main(["close", "--fixture", "win"]) == 0
    closed = json.loads(capsys.readouterr().out)
    assert closed["status"] == "CLOSED" and closed["verified"] is False

    assert cli.main(["report"]) == cli.EXIT["INSUFFICIENT"] == 4
    report = json.loads(capsys.readouterr().out)
    assert report["independent_forward_sessions"] == 0
    assert report["real_money_enabled"] is False
    assert report["tier"] == "HISTORICAL_REPLAY"
    assert "REAL_QUOTES_NOT_AVAILABLE" in report["missing_data"]
