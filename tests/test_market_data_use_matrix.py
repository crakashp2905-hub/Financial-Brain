from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts import market_data_use_matrix as matrix


def test_source_hash_is_invariant_to_windows_and_linux_line_endings(tmp_path: Path) -> None:
    windows = tmp_path / "windows.txt"
    linux = tmp_path / "linux.txt"
    windows.write_bytes(b"first\r\nsecond\r\n")
    linux.write_bytes(b"first\nsecond\n")

    assert matrix._sha(windows) == matrix._sha(linux)
    assert matrix._sha(linux) == hashlib.sha256(b"first\nsecond\n").hexdigest()


def test_published_matrix_matches_normalized_source_and_rights_hashes() -> None:
    published = json.loads(matrix.OUTPUT.read_text(encoding="utf-8"))
    expected = matrix.build()

    assert published == expected
    assert published["source_registry_sha256"] == matrix._sha(matrix.SOURCES)
    assert published["permission_register_sha256"] == matrix._sha(matrix.PERMISSIONS)
    assert published["rights_review_sha256"] == matrix._sha(matrix.RIGHTS_REVIEW)


def test_unapproved_provider_data_uses_remain_operationally_blocked() -> None:
    report = matrix.build()
    providers = [row for row in report["sources"] if row["source_id"] != "LOCAL_DETERMINISTIC_SYNTHETIC_FIXTURES"]

    assert len(providers) == report["source_count"]
    assert all(
        use["status"] in {"UNKNOWN_RIGHTS", "NOT_APPLICABLE"} and use["blocks_operational_use"]
        for row in providers
        for use in row["uses"].values()
    )
