"""Strategy and model registries with immutable identity and governed states (FB-0047, FB-0131, FB-0132, FB-0141, FB-0471, FB-0472, FB-0477, FB-0492, FB-0493).

* ``StrategySpec`` is canonicalised to JSON; its hash is the identity. Equal specs hash equal on every run.
* ``StrategyRegistry`` refuses to overwrite a (id, version) with different content.
* ``ModelRegistry`` governs ``DRAFT -> SHADOW -> PAPER``, with ``SUSPENDED`` reachable from any state. Each move needs
  its gates passed. Only paper promotion is automatic; a challenger that fails a gate stays in its prior state.
* Artifacts are hashed; loading a modified artifact under an old hash is refused.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any


class RegistryError(ValueError):
    pass


FORWARD_STATUSES = frozenset({"FAILED", "EXPLORATORY", "SHADOW_ONLY", "PAPER_ELIGIBLE",
                              "FROZEN_CHALLENGER", "VERIFIED", "RETIRED"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def canonical_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    version: str
    params: dict[str, Any]
    owner: str
    family: str = ""
    code_sha256: str = ""
    asset_scope: tuple[str, ...] = ()
    decision_clock: str = ""
    direction: str = ""
    stop_exit: str = ""
    horizon: str = ""
    data_requirements: tuple[str, ...] = ()

    def hash(self) -> str:
        return canonical_hash({"id": self.strategy_id, "version": self.version, "params": self.params,
                               "owner": self.owner, "family": self.family, "code_sha256": self.code_sha256,
                               "asset_scope": self.asset_scope, "decision_clock": self.decision_clock,
                               "direction": self.direction, "stop_exit": self.stop_exit, "horizon": self.horizon,
                               "data_requirements": self.data_requirements})

    def to_json(self) -> str:
        return json.dumps({"id": self.strategy_id, "version": self.version, "params": self.params,
                           "owner": self.owner, "family": self.family, "code_sha256": self.code_sha256,
                           "asset_scope": list(self.asset_scope), "decision_clock": self.decision_clock,
                           "direction": self.direction, "stop_exit": self.stop_exit, "horizon": self.horizon,
                           "data_requirements": list(self.data_requirements)}, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> StrategySpec:
        d = json.loads(text)
        return cls(d["id"], d["version"], d["params"], d["owner"], d.get("family", ""),
                   d.get("code_sha256", ""), tuple(d.get("asset_scope", ())), d.get("decision_clock", ""),
                   d.get("direction", ""), d.get("stop_exit", ""), d.get("horizon", ""),
                   tuple(d.get("data_requirements", ())))

    def validate_forward(self) -> None:
        """Validate the complete frozen identity needed for forward-edge registration."""
        if not all((self.strategy_id.strip(), self.version.strip(), self.owner.strip(), self.family.strip(),
                    self.decision_clock.strip(), self.direction.strip(), self.stop_exit.strip(), self.horizon.strip())):
            raise RegistryError("forward strategy spec is missing a required identity or trading contract field")
        if self.direction not in {"LONG", "SHORT", "LONG_SHORT", "FLAT_ONLY"}:
            raise RegistryError("invalid strategy direction")
        if not _SHA256.fullmatch(self.code_sha256):
            raise RegistryError("code_sha256 must be a lowercase SHA-256 digest")
        if not self.asset_scope or not self.data_requirements:
            raise RegistryError("asset_scope and data_requirements are required")
        if not isinstance(self.params, dict):
            raise RegistryError("params must be an object")


@dataclass(frozen=True)
class StrategyEligibility:
    strategy_id: str
    version: str
    status: str
    source_ref: str
    source_sha256: str
    exclusion_reason: str
    spec_sha256: str

    def __post_init__(self) -> None:
        if self.status not in FORWARD_STATUSES:
            raise RegistryError(f"unknown forward strategy status {self.status!r}")
        if not all((self.strategy_id.strip(), self.version.strip(), self.source_ref.strip(),
                    self.exclusion_reason.strip())):
            raise RegistryError("eligibility needs identity, lineage and exclusion reason")
        if not _SHA256.fullmatch(self.source_sha256) or not _SHA256.fullmatch(self.spec_sha256):
            raise RegistryError("eligibility source and spec hashes must be SHA-256 digests")


class StrategyRegistry:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], StrategySpec] = {}
        self._hashes: dict[tuple[str, str], str] = {}

    def register(self, spec: StrategySpec) -> str:
        key = (spec.strategy_id, spec.version)
        h = spec.hash()
        if key in self._items:
            if self._hashes[key] == h:
                return h
            raise RegistryError(f"{key} already registered with different content; versions are immutable")
        self._items[key] = spec
        self._hashes[key] = h
        return h

    def get(self, strategy_id: str, version: str) -> StrategySpec:
        return self._items[(strategy_id, version)]


STATES = ("DRAFT", "SHADOW", "PAPER", "SUSPENDED")
_ALLOWED = {
    "DRAFT": {"SHADOW", "SUSPENDED"},
    "SHADOW": {"PAPER", "DRAFT", "SUSPENDED"},
    "PAPER": {"SUSPENDED", "SHADOW"},
    "SUSPENDED": {"DRAFT"},
}


@dataclass
class ModelRecord:
    model_id: str
    artifact_sha256: str
    state: str = "DRAFT"
    history: list[tuple[str, str, str]] = field(default_factory=list)


class ModelRegistry:
    def __init__(self) -> None:
        self._models: dict[str, ModelRecord] = {}

    def propose(self, model_id: str, artifact: bytes) -> ModelRecord:
        if model_id in self._models:
            raise RegistryError("model id already proposed")
        rec = ModelRecord(model_id, hashlib.sha256(artifact).hexdigest())
        self._models[model_id] = rec
        return rec

    def move(self, model_id: str, to: str, *, gates_passed: bool, reason: str) -> ModelRecord:
        rec = self._models[model_id]
        if to not in STATES:
            raise RegistryError(f"unknown state {to}")
        if to not in _ALLOWED[rec.state]:
            raise RegistryError(f"illegal transition {rec.state} -> {to}")
        if to in ("SHADOW", "PAPER") and not gates_passed:
            raise RegistryError(f"gates not passed; {model_id} stays {rec.state}")
        if to == "PAPER" and rec.state != "SHADOW":
            raise RegistryError("paper promotion must follow shadow evaluation")
        rec.history.append((rec.state, to, reason))
        rec.state = to
        return rec

    def load(self, model_id: str, artifact: bytes) -> None:
        rec = self._models[model_id]
        if hashlib.sha256(artifact).hexdigest() != rec.artifact_sha256:
            raise RegistryError("artifact hash mismatch; refusing to load modified weights")
        if rec.state == "SUSPENDED":
            raise RegistryError("suspended model cannot score")
