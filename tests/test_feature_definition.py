from __future__ import annotations

import hashlib

import pytest

from financial_brain.features.definition import FeatureDefinition, FeatureDefinitionError


def _definition(**overrides) -> FeatureDefinition:
    value = {
        "feature_id": "MOMENTUM_12_1",
        "version": "v1.0",
        "formula": "close[t-21] / close[t-252] - 1",
        "window": 252,
        "market": "NSE_EQ",
        "horizon": "20_sessions",
        "input_lineage": ("adjusted_close",),
        "lag": 21,
        "leakage_test_ids": ("test_momentum_uses_only_past_closes",),
        "data_type": "float",
    }
    value.update(overrides)
    return FeatureDefinition(**value)


def test_definition_is_canonical_and_binds_formula_and_contract_hashes() -> None:
    definition = _definition()
    same = _definition(formula="close[t-21] / close[t-252] - 1   ")
    assert definition.formula_sha256 == hashlib.sha256(
        b"close[t-21] / close[t-252] - 1").hexdigest()
    assert definition.definition_sha256 == same.definition_sha256
    assert definition.as_dict()["formula_sha256"] == definition.formula_sha256
    assert len(definition.definition_sha256) == 64


def test_definition_round_trip_is_stable_and_detects_formula_tampering() -> None:
    definition = _definition()
    restored = FeatureDefinition.from_dict(definition.as_dict())
    assert restored == definition
    assert restored.definition_sha256 == definition.definition_sha256
    changed = definition.as_dict() | {"formula": "close[t] / close[t-252] - 1"}
    with pytest.raises(FeatureDefinitionError, match="does not match"):
        FeatureDefinition.from_dict(changed)


@pytest.mark.parametrize("override,match", [
    ({"window": 0}, "positive integer"),
    ({"lag": -1}, "non-negative integer"),
    ({"market": " "}, "market is required"),
    ({"horizon": ""}, "horizon is required"),
    ({"input_lineage": ()}, "input_lineage"),
    ({"leakage_test_ids": ()}, "leakage_test_ids"),
    ({"data_type": "object"}, "data_type"),
    ({"version": " "}, "version"),
])
def test_incomplete_or_invalid_definition_is_rejected(override, match) -> None:
    with pytest.raises(FeatureDefinitionError, match=match):
        _definition(**override)
