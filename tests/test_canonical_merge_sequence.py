import copy
import json

import pytest

from scripts.validate_merge_sequence import SNAPSHOT, validate


def test_canonical_sequence_derives_exact_branch_and_sha_dependencies():
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    validate(snapshot)
    assert snapshot["sequence"] == [37, 36, 38]
    assert snapshot["nodes"]["37"]["base_sha"] != snapshot["main_sha_at_capture"]
    assert "green hosted checks" in snapshot["merge_gates"][0]


def test_sequence_rejects_reordered_or_stale_dependency_edges():
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    reordered = copy.deepcopy(snapshot)
    reordered["sequence"] = [36, 37, 38]
    with pytest.raises(ValueError, match="PR 37, then PR 36"):
        validate(reordered)
    stale = copy.deepcopy(snapshot)
    stale["nodes"]["38"]["base_sha"] = "0" * 40
    with pytest.raises(ValueError, match="PR 38 does not exactly depend"):
        validate(stale)
