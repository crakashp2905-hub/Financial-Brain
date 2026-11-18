import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> dict:
    path = ROOT / "docs" / "edge-program" / name
    return json.loads(path.read_text(encoding="utf-8"))


def test_source_matrix_labels_rights_latency_and_credential_presence_without_values():
    matrix = _read("data-source-matrix.json")
    sources = matrix["sources"]
    assert {row["status"] for row in sources} <= {"MISSING", "LICENSED", "UNVERIFIED", "READY"}
    assert len({row["id"] for row in sources}) == len(sources)
    for row in sources:
        assert row["domains"]
        assert isinstance(row["access"], dict)
        assert "credential_flag_present" in row["access"] or row["id"] == "kite-connect"
        assert row["latency"]["observed_request_latency_ms"] is None or isinstance(
            row["latency"]["observed_request_latency_ms"], (int, float)
        )
        assert isinstance(row["retention_rights"], str) and row["retention_rights"]
        for source_path in row["source_code"]:
            assert (ROOT / source_path).is_file(), source_path
    kite = next(row for row in sources if row["id"] == "kite-connect")
    assert kite["access"]["api_key_environment_flag_present"] is False
    assert kite["access"]["access_token_environment_flag_present"] is False
    assert kite["access"]["token_file_present"] is True
    assert kite["access"]["credential_validity"] == "NOT_AUTHENTICATED_IN_THIS_AUDIT"
    assert {row["id"] for row in sources if row["status"] == "MISSING"} >= {
        "us-equity-quotes", "expired-options-history"
    }
    assert all("LICENSED" not in json.dumps(row) for row in sources if row["status"] == "UNVERIFIED")
    assert "https://www.niftyindices.com/BackPage/getTotalReturnIndexString" in next(
        row for row in sources if row["id"] == "nifty-indices"
    )["endpoints"]


def test_mode_matrix_has_tested_mode_fidelity_and_evidence_for_every_displayed_capability():
    matrix = _read("mode-capability-matrix.json")
    rows = matrix["modes"]
    assert {row["mode"] for row in rows} == {
        "REAL_FEED", "REPLAY", "SHADOW", "PAPER", "BROKER_DRYRUN", "LIVE_CASH"
    }
    for row in rows:
        assert row["tested_mode"]
        assert row["fidelity"]
        assert row["evidence_paths"]
        for item in row["evidence_paths"]:
            source_path = item.split("::", maxsplit=1)[0]
            assert (ROOT / source_path).is_file(), source_path
    assert next(row for row in rows if row["mode"] == "LIVE_CASH")["status"] == "DISABLED"
    assert next(row for row in rows if row["mode"] == "BROKER_DRYRUN")["status"] == "BLOCKED"
