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
from dataclasses import dataclass, field
from typing import Any


class RegistryError(ValueError):
    pass


def canonical_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    version: str
    params: dict[str, Any]
    owner: str

    def hash(self) -> str:
        return canonical_hash({"id": self.strategy_id, "version": self.version, "params": self.params,
                               "owner": self.owner})

    def to_json(self) -> str:
        return json.dumps({"id": self.strategy_id, "version": self.version, "params": self.params,
                           "owner": self.owner}, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> StrategySpec:
        d = json.loads(text)
        return cls(d["id"], d["version"], d["params"], d["owner"])


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
