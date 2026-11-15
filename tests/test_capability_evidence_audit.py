from __future__ import annotations

import json
from pathlib import Path

from scripts.audit_capability_evidence import build, render


def test_capability_manifest_binds_every_curated_source_and_test(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src/a.py").write_text("SOURCE = 1\n", encoding="utf-8")
    (tmp_path / "tests/test_a.py").write_text("def test_a(): pass\n", encoding="utf-8")
    matrix = {"capabilities": [{"id": "a", "maturity": "local_tested",
                                "source": ["src/a.py"], "tests": ["tests/test_a.py"]}]}

    first = build(matrix, tmp_path)
    evidence = first["capabilities"][0]
    (tmp_path / "src/a.py").write_text("SOURCE = 2\n", encoding="utf-8")
    second = build(matrix, tmp_path)

    assert evidence["sources"]["src/a.py"] != second["capabilities"][0]["sources"]["src/a.py"]
    assert first["matrix_sha256"] == second["matrix_sha256"]
    assert first["manifest_sha256"] != second["manifest_sha256"]
    assert json.loads(render(first))["capabilities"][0]["artifact_sha256"] == evidence["artifact_sha256"]


def test_evidence_artifact_digest_binds_test_file_hashes(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src/a.py").write_text("SOURCE = 1\n", encoding="utf-8")
    (tmp_path / "tests/test_a.py").write_text("def test_a(): pass\n", encoding="utf-8")
    matrix = {"capabilities": [{"id": "a", "maturity": "local_tested",
                                "source": ["src/a.py"], "tests": ["tests/test_a.py"]}]}
    before = build(matrix, tmp_path)
    (tmp_path / "tests/test_a.py").write_text("def test_a(): assert True\n", encoding="utf-8")
    after = build(matrix, tmp_path)
    assert before["capabilities"][0]["artifact_sha256"] != after["capabilities"][0]["artifact_sha256"]


def test_manifest_hash_is_stable_across_windows_and_unix_line_endings(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    source = tmp_path / "src/a.py"
    test = tmp_path / "tests/test_a.py"
    source.write_bytes(b"VALUE = 1\n")
    test.write_bytes(b"def test_a():\n    assert True\n")
    matrix = {"capabilities": [{"id": "a", "maturity": "local_tested",
                                "source": ["src/a.py"], "tests": ["tests/test_a.py"]}]}
    lf_manifest = build(matrix, tmp_path)

    source.write_bytes(b"VALUE = 1\r\n")
    test.write_bytes(b"def test_a():\r\n    assert True\r\n")
    crlf_manifest = build(matrix, tmp_path)

    assert render(lf_manifest) == render(crlf_manifest)
