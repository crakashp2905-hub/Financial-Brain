import json

import pytest

from scripts import profit_readiness


def test_readiness_is_gate_based_and_links_exact_evidence():
    report = profit_readiness.build_report()
    assert report["overall"] == "RED"
    assert report["decision"] == "NOT_READY_FOR_PROFITABILITY_CLAIM"
    assert all("percent" not in key.lower() for key in report)
    assert all(gate["status"] in {"RED", "AMBER", "GREEN"} for gate in report["gates"])
    assert all(gate["evidence"] and all(len(item["sha256"]) == 64 for item in gate["evidence"])
               for gate in report["gates"])
    assert next(g for g in report["gates"] if g["id"] == "prospective_net_profitability")["status"] == "RED"
    assert next(g for g in report["gates"] if g["id"] == "physical_order_safety")["status"] == "GREEN"


def test_readiness_rejects_percentage_fields_and_check_detects_edits(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="percentage-based"):
        profit_readiness._reject_percentages({"gates": {"completion_percent": 99}})
    output = tmp_path / "profit_readiness.json"
    monkeypatch.setattr(profit_readiness, "OUTPUT", output)
    output.write_text('{"overall":"GREEN"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="stale or hand-edited"):
        profit_readiness.validate_output()
    expected = profit_readiness.render()
    assert json.loads(expected)["overall"] == "RED"
    assert output.read_text(encoding="utf-8") != expected
