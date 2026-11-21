"""Coverage, CLI, gates, snapshots and feed checks (FB-0417, FB-0620, FB-0627, FB-0643, FB-0644).

* ``coverage_matrix`` lists every module, persona and asset-class cell and the cells with no test.
* ``build_parser`` is the research CLI with its six subcommands.
* ``coverage_gate`` returns the areas below their minimum coverage percentage.
* ``snapshot_digest`` is a stable digest of an object, used to compare snapshots.
* ``feed_check`` counts duplicates, gaps and reordered messages in a sequence-numbered feed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from itertools import product
from typing import Any

RESEARCH_COMMANDS = ("doctor", "data", "backtest", "optimize", "replay", "paper")


def coverage_matrix(
    modules: Sequence[str], personas: Sequence[str], asset_classes: Sequence[str], tested: set[tuple[str, str, str]]
) -> dict[str, Any]:
    cells = list(product(modules, personas, asset_classes))
    missing = sorted(c for c in cells if c not in tested)
    return {"cells": len(cells), "missing": missing}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in RESEARCH_COMMANDS:
        sub.add_parser(name)
    return parser


def coverage_gate(percent: Mapping[str, float], thresholds: Mapping[str, float]) -> list[str]:
    failing = []
    for area, minimum in thresholds.items():
        if percent.get(area, 0.0) < minimum:
            failing.append(area)
    return sorted(failing)


def snapshot_digest(obj: Any) -> str:
    body = json.dumps(obj, sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def feed_check(seqs: Sequence[int]) -> dict[str, int]:
    duplicates = gaps = reordered = 0
    for prev, cur in zip(seqs, seqs[1:], strict=False):
        if cur == prev:
            duplicates += 1
        elif cur < prev:
            reordered += 1
        elif cur - prev > 1:
            gaps += 1
    return {"duplicates": duplicates, "gaps": gaps, "reordered": reordered}
