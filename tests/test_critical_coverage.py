from __future__ import annotations

from pathlib import Path

from scripts.check_critical_coverage import evaluate


def _setup(tmp_path: Path):
    domains = {
        "oms": ["src/financial_brain/execution/oms/core.py"],
        "custody": ["src/financial_brain/forward_edge/custody.py"],
        "market_data": ["src/financial_brain/providers/kite_data.py"],
        "accounting": ["src/financial_brain/kernel/paper_account.py"],
        "evaluation": ["src/financial_brain/evaluation/champion.py"],
        "risk": ["src/financial_brain/risk/limits.py"],
    }
    for paths in domains.values():
        for name in paths:
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("pass\n", encoding="utf-8")
    policy = {"domains": {
        name: {"minimum_percent": 80.0, "patterns": paths}
        for name, paths in domains.items()
    }}
    report = {"files": {
        name: {"summary": {"num_statements": 10, "covered_lines": 9}}
        for paths in domains.values() for name in paths
    }}
    return report, policy


def test_critical_coverage_passes_only_when_all_domain_floors_are_met(tmp_path):
    report, policy = _setup(tmp_path)
    result = evaluate(report, policy, tmp_path)
    assert result["status"] == "PASS"
    assert set(result["domains"]) == {"oms", "custody", "market_data", "accounting", "evaluation", "risk"}


def test_critical_coverage_fails_on_regression_or_missing_file_record(tmp_path):
    report, policy = _setup(tmp_path)
    report["files"]["src/financial_brain/kernel/paper_account.py"]["summary"]["covered_lines"] = 3
    report["files"].pop("src/financial_brain/risk/limits.py")
    result = evaluate(report, policy, tmp_path)
    assert result["status"] == "FAIL"
    assert result["domains"]["accounting"]["coverage_percent"] == 30.0
    assert result["domains"]["risk"]["missing_coverage_records"] == ["src/financial_brain/risk/limits.py"]
