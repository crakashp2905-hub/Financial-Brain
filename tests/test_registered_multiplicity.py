import pytest

from financial_brain.forward_edge.evidence_stats import StatsError, adjust_registered_family


def test_familywise_adjustment_counts_unrun_and_failed_registered_trials():
    family = [f"strategy-{index:03d}" for index in range(100)]
    p_values = {family[0]: 0.001, family[1]: 0.02, family[2]: None}

    report = adjust_registered_family(family, p_values)

    assert report["family_size"] == 100
    assert report["tested_count"] == 2
    assert report["missing_p_value_count"] == 98
    assert report["missing_trial_ids"] == sorted(family[2:])
    assert report["trials"][family[0]]["adjusted_p_value"] == pytest.approx(0.1)
    assert report["trials"][family[2]]["p_value_used_for_adjustment"] == 1.0
    assert len(report["family_sha256"]) == 64


def test_familywise_adjustment_rejects_registry_and_p_value_mismatch():
    with pytest.raises(StatsError, match="unique"):
        adjust_registered_family(["a", "a"], {"a": 0.01})
    with pytest.raises(StatsError, match="unregistered"):
        adjust_registered_family(["a"], {"b": 0.01})
    with pytest.raises(StatsError, match="invalid p-value"):
        adjust_registered_family(["a"], {"a": 1.1})
