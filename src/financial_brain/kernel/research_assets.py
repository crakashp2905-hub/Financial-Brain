"""Research assets (FB-0418, FB-0423, FB-0424, FB-0426, FB-0428).

* ``as_of_snapshot`` returns rows whose availability time is at or before the requested instant. Later rows are never
  returned, so a request cannot see the future.
* ``MaterializationCache`` stores a derived result with the dependencies it was built from. Changing one dependency
  marks every dependent entry dirty, and dirty entries are not served.
* ``result_diff`` compares two result dictionaries and lists keys that were added, removed or changed.
* ``ResearchGraph`` links assets (hypotheses, datasets, models, runs, papers). Edges must reference known nodes.
* ``package_strategy`` builds a manifest from a notebook's source with a content hash, so the packaged strategy can be
  checked against the notebook it came from.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Row:
    key: str
    available_at: datetime
    value: float


def as_of_snapshot(rows: Sequence[Row], as_of: datetime) -> dict[str, float]:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    latest: dict[str, Row] = {}
    for row in rows:
        if row.available_at > as_of:
            continue
        current = latest.get(row.key)
        if current is None or row.available_at > current.available_at:
            latest[row.key] = row
    return {k: r.value for k, r in latest.items()}


@dataclass
class MaterializationCache:
    entries: dict[str, tuple[frozenset[str], Any]] = field(default_factory=dict)
    dirty: set[str] = field(default_factory=set)
    _deps: dict[str, frozenset[str]] = field(default_factory=dict)

    def store(self, name: str, depends_on: frozenset[str], value: Any) -> None:
        self.entries[name] = (depends_on, value)
        self._deps[name] = depends_on
        self.dirty.discard(name)

    def invalidate(self, changed: str) -> set[str]:
        hit = {name for name, deps in self._deps.items() if changed in deps}
        self.dirty |= hit
        return hit

    def get(self, name: str) -> Any:
        if name in self.dirty:
            raise KeyError(f"{name} is dirty; rebuild before use")
        return self.entries[name][1]


def result_diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, list[str]]:
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(k for k in set(before) & set(after) if before[k] != after[k])
    return {"added": added, "removed": removed, "changed": changed}


class ResearchGraph:
    KINDS = frozenset({"hypothesis", "dataset", "model", "run", "paper"})

    def __init__(self) -> None:
        self.nodes: dict[str, str] = {}
        self.edges: set[tuple[str, str]] = set()

    def add_node(self, node_id: str, kind: str) -> None:
        if kind not in self.KINDS:
            raise ValueError(f"unknown kind {kind!r}")
        if node_id in self.nodes:
            raise ValueError(f"node {node_id} already exists")
        self.nodes[node_id] = kind

    def link(self, src: str, dst: str) -> None:
        if src not in self.nodes or dst not in self.nodes:
            raise KeyError("both ends of an edge must be known nodes")
        self.edges.add((src, dst))


def package_strategy(notebook_source: str, name: str, version: str) -> dict[str, str]:
    digest = hashlib.sha256(notebook_source.encode()).hexdigest()
    manifest = {"name": name, "version": version, "source_sha256": digest}
    return {"manifest": json.dumps(manifest, sort_keys=True), "sha256": digest}
