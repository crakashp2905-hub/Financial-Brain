import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_health_axes_are_separate_and_current_research_does_not_overclaim():
    path = ROOT / "docs/edge-program/release-semantics.json"
    policy = json.loads(path.read_text(encoding="utf-8"))
    axes = policy["axes"]
    assert set(axes) == {"engineering", "research", "trading"}
    for axis in axes.values():
        assert set(axis) == {"GREEN", "YELLOW", "RED"}
        assert all(axis[level] for level in ("GREEN", "YELLOW", "RED"))
    assert any("GREEN engineering does not imply GREEN research" in item
               for item in policy["non_implications"])
    assert policy["current_assessment"]["research"].startswith("YELLOW")
    assert policy["current_assessment"]["trading"].startswith("YELLOW")
    assert policy["current_assessment"]["live_cash"] == "DISABLED"
    assert len(policy["non_implications"]) == 3
    for source in policy["evidence_sources"]:
        assert (ROOT / source).is_file(), source
