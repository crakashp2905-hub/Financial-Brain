from __future__ import annotations

from scripts.capability_provenance import OUTPUT, build_manifest, validate


def test_manifest_separates_main_and_each_requested_pull_request_source():
    manifest = build_manifest()
    sources = {source["source_id"]: source for source in manifest["sources"]}

    assert set(sources) == {"main", "PR-34", "PR-36", "PR-37", "PR-38", "PR-39", "PR-40"}
    assert len(sources["main"]["commit_sha"]) == 40
    for source_id in ("PR-34", "PR-36", "PR-37", "PR-38", "PR-39", "PR-40"):
        assert len(sources[source_id]["head_sha"]) == 40
        assert len(sources[source_id]["base_sha"]) == 40
        assert sources[source_id]["changed_paths"]

    assert sources["PR-36"]["depends_on"] == ["PR-37"]
    assert sources["PR-38"]["depends_on"] == ["PR-36"]
    assert sources["PR-40"]["merged"] is True
    assert sources["PR-40"]["state"] == "closed"


def test_manifest_records_exact_source_capability_registry_gaps_and_path_mapping():
    sources = {source["source_id"]: source for source in build_manifest()["sources"]}

    assert sources["main"]["capability_registry"]["present"] is True
    assert sources["PR-34"]["capability_registry_at_head"]["present"] is True
    assert sources["PR-36"]["capability_registry_at_head"]["present"] is False
    assert sources["PR-36"]["capability_path_mapping"]["retrospective_mapping"] is True
    assert sources["PR-40"]["capability_registry_at_head"]["present"] is True
    assert sources["PR-40"]["module_inventory_delta"] is None


def test_manifest_matches_git_graph_and_historical_source_snapshot():
    assert OUTPUT.exists()
    assert validate() == []
