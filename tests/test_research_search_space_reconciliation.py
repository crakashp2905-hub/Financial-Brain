from __future__ import annotations

import json

from scripts.validate_research_search_space import REPORT, ROOT, validate


def _report():
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_search_space_reconciliation_keeps_known_counts_and_conflict_explicit():
    report = _report()

    assert validate(report) == []
    assert sum(report["documented_historical_family_counts"].values()) == 150
    assert report["claims"]["historical_total_counts"] == [150, 151]
    assert report["claims"]["global_search_look_budget"] is None


def test_search_space_reconciliation_rejects_changed_historical_source(tmp_path):
    report = _report()
    source = report["sources"][0]
    target = tmp_path / source["path"]
    target.parent.mkdir(parents=True)
    original = ROOT.joinpath(source["path"]).read_bytes()
    target.write_bytes(original + b"changed")

    assert any("fingerprint changed" in error for error in validate(report, tmp_path))


def test_search_space_reconciliation_rejects_false_reconciled_or_miscounted_totals():
    report = _report()
    report["claims"]["historical_total_reconciled"] = True

    assert any("must not be marked reconciled" in error for error in validate(report))

    report = _report()
    report["documented_historical_family_counts"]["momentum"] += 1
    assert any("must sum to 150" in error for error in validate(report))
