from __future__ import annotations

from datetime import datetime, timezone

import pytest

from financial_brain.kernel.research_assets import (
    MaterializationCache,
    ResearchGraph,
    Row,
    as_of_snapshot,
    package_strategy,
    result_diff,
)

D1 = datetime(2026, 11, 1, tzinfo=timezone.utc)
D2 = datetime(2026, 11, 5, tzinfo=timezone.utc)
D3 = datetime(2026, 11, 9, tzinfo=timezone.utc)


def test_as_of_snapshot_never_returns_future_rows() -> None:
    rows = [Row("A", D1, 1.0), Row("A", D3, 9.0), Row("B", D2, 2.0)]
    assert as_of_snapshot(rows, D2) == {"A": 1.0, "B": 2.0}
    with pytest.raises(ValueError):
        as_of_snapshot(rows, datetime(2026, 11, 2))


def test_dirty_dependency_blocks_stale_cache_use() -> None:
    cache = MaterializationCache()
    cache.store("features", frozenset({"prices"}), [1, 2])
    assert cache.get("features") == [1, 2]
    assert cache.invalidate("prices") == {"features"}
    with pytest.raises(KeyError, match="dirty"):
        cache.get("features")
    cache.store("features", frozenset({"prices"}), [3])
    assert cache.get("features") == [3]


def test_result_diff_lists_added_removed_and_changed() -> None:
    diff = result_diff({"a": 1, "b": 2}, {"a": 1, "b": 3, "c": 4})
    assert diff == {"added": ["c"], "removed": [], "changed": ["b"]}


def test_graph_refuses_unknown_kinds_and_dangling_edges() -> None:
    g = ResearchGraph()
    g.add_node("h1", "hypothesis")
    g.add_node("d1", "dataset")
    g.link("d1", "h1")
    with pytest.raises(ValueError, match="unknown kind"):
        g.add_node("x", "spreadsheet")
    with pytest.raises(KeyError):
        g.link("d1", "missing")


def test_packaging_hash_changes_with_the_notebook() -> None:
    a = package_strategy("print(1)", "orb", "1.0")
    assert a == package_strategy("print(1)", "orb", "1.0")
    assert a["sha256"] != package_strategy("print(2)", "orb", "1.0")["sha256"]
