from scripts import check_critical_coverage as gate


def _report(percent=100.0):
    return {"files": {
        f"src/financial_brain/{path}": {"summary": {"percent_covered": percent}}
        for path in gate.FLOORS
    }}


def test_per_module_coverage_policy_passes_when_all_scoped_floors_are_met():
    assert gate.check(_report()) == []


def test_one_weak_service_is_not_hidden_by_other_modules():
    report = _report()
    report["files"]["src/financial_brain/risk/stress.py"]["summary"]["percent_covered"] = 89.9
    assert gate.check(report) == ["risk/stress.py: 89.9% is below the 90.0% floor"]


def test_missing_module_and_malformed_report_fail_closed():
    report = _report()
    del report["files"]["src/financial_brain/interfaces/mcp_server.py"]
    errors = gate.check(report)
    assert errors == ["interfaces/mcp_server.py: expected one coverage record, found 0"]
    assert gate.check({}) == ["coverage JSON must contain a files object"]
