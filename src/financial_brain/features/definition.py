"""Canonical, content-addressed feature definitions (SL-02-01).

Definitions describe feature semantics and lineage; they do not execute formulas. Runtime
feature builders remain responsible for causal values, while the definition gives snapshots,
training manifests and leakage tests a stable identity to bind to.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass


class FeatureDefinitionError(ValueError):
    """Feature identity or causal metadata is incomplete or inconsistent."""


_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_DATA_TYPES = frozenset({"bool", "category", "decimal", "float", "int", "string"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class FeatureDefinition:
    feature_id: str
    version: str
    formula: str
    window: int
    market: str
    horizon: str
    input_lineage: tuple[str, ...]
    lag: int
    leakage_test_ids: tuple[str, ...]
    data_type: str

    def __post_init__(self) -> None:
        if not isinstance(self.feature_id, str) or not _ID.fullmatch(self.feature_id):
            raise FeatureDefinitionError("feature_id must be a stable identifier")
        if not isinstance(self.version, str) or not _VERSION.fullmatch(self.version):
            raise FeatureDefinitionError("version must be a stable version identifier")
        if not isinstance(self.formula, str) or not self.formula.strip():
            raise FeatureDefinitionError("formula must be explicit and non-empty")
        normalized_formula = "\n".join(line.rstrip() for line in self.formula.strip().splitlines())
        object.__setattr__(self, "formula", normalized_formula)
        if type(self.window) is not int or self.window < 1:
            raise FeatureDefinitionError("window must be a positive integer")
        if not isinstance(self.market, str) or not self.market.strip():
            raise FeatureDefinitionError("market is required")
        if not isinstance(self.horizon, str) or not self.horizon.strip():
            raise FeatureDefinitionError("horizon is required")
        if type(self.lag) is not int or self.lag < 0:
            raise FeatureDefinitionError("lag must be a non-negative integer")
        if self.data_type not in _DATA_TYPES:
            raise FeatureDefinitionError(f"data_type must be one of {sorted(_DATA_TYPES)}")
        for name in ("input_lineage", "leakage_test_ids"):
            values = getattr(self, name)
            if (not isinstance(values, tuple) or not values
                    or any(not isinstance(value, str) or not value.strip() for value in values)
                    or len(values) != len(set(values))):
                raise FeatureDefinitionError(f"{name} must be a non-empty tuple of unique identifiers")
        if any(not _ID.fullmatch(value) for value in (*self.input_lineage, *self.leakage_test_ids)):
            raise FeatureDefinitionError("input lineage and leakage test IDs must be stable identifiers")

    @property
    def formula_sha256(self) -> str:
        """Hash the normalized formula text independently from the rest of the contract."""
        normalized = "\n".join(line.rstrip() for line in self.formula.strip().splitlines())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def canonical(self) -> bytes:
        payload = asdict(self)
        payload["input_lineage"] = list(self.input_lineage)
        payload["leakage_test_ids"] = list(self.leakage_test_ids)
        payload["formula_sha256"] = self.formula_sha256
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False).encode("utf-8")

    @property
    def definition_sha256(self) -> str:
        return hashlib.sha256(self.canonical()).hexdigest()

    def as_dict(self) -> dict[str, object]:
        return json.loads(self.canonical().decode("utf-8"))

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "FeatureDefinition":
        expected = {"feature_id", "version", "formula", "formula_sha256", "window", "market", "horizon",
                    "input_lineage", "lag", "leakage_test_ids", "data_type"}
        if set(value) != expected:
            raise FeatureDefinitionError("feature definition has missing or unknown fields")
        formula = value["formula"]
        if not isinstance(formula, str):
            raise FeatureDefinitionError("formula must be text")
        if not isinstance(value["formula_sha256"], str) or not _SHA256.fullmatch(value["formula_sha256"]):
            raise FeatureDefinitionError("formula_sha256 must be lowercase SHA-256")
        input_lineage = value["input_lineage"]
        leakage_test_ids = value["leakage_test_ids"]
        if not isinstance(input_lineage, list) or any(not isinstance(item, str) for item in input_lineage):
            raise FeatureDefinitionError("input_lineage must be a list of strings")
        if not isinstance(leakage_test_ids, list) or any(not isinstance(item, str) for item in leakage_test_ids):
            raise FeatureDefinitionError("leakage_test_ids must be a list of strings")
        feature_id, version = value["feature_id"], value["version"]
        window = value["window"]
        market, horizon, lag, data_type = (value["market"], value["horizon"], value["lag"], value["data_type"])
        if not isinstance(feature_id, str) or not isinstance(version, str) or not isinstance(formula, str):
            raise FeatureDefinitionError("feature ID, version and formula must be text")
        if (type(window) is not int or not isinstance(market, str) or not isinstance(horizon, str)
                or type(lag) is not int or not isinstance(data_type, str)):
            raise FeatureDefinitionError("window/lag must be integers and market/horizon/data_type must be text")
        definition = cls(
            feature_id=feature_id, version=version, formula=formula,
            window=window, market=market, horizon=horizon,
            input_lineage=tuple(input_lineage), lag=lag,
            leakage_test_ids=tuple(leakage_test_ids), data_type=data_type,
        )
        if definition.formula_sha256 != value["formula_sha256"]:
            raise FeatureDefinitionError("formula hash does not match formula text")
        return definition


__all__ = ["FeatureDefinition", "FeatureDefinitionError"]
