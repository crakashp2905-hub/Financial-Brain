"""FE-0009: disposable DuckDB ledgers never overlap or overwrite repository data."""
from __future__ import annotations

from pathlib import Path

import pytest

from financial_brain.forward_edge.disposable_ledger import (
    DisposableLedgerError,
    create_disposable_ledger,
    temporary_disposable_ledger,
)

REPO = Path(__file__).resolve().parents[1]


def test_disposable_ledger_isolated_append_only_and_removed_on_close(tmp_path: Path) -> None:
    with create_disposable_ledger(tmp_path, repo_root=REPO) as ledger:
        assert ledger.append("evt-1", {"kind": "fill", "qty": 2})
        assert not ledger.append("evt-1", {"qty": 2, "kind": "fill"})
        con = ledger.connect()
        try:
            assert con.execute("SELECT count(*) FROM events").fetchone() == (1,)
            assert con.execute("SELECT value FROM ledger_meta WHERE key='schema'").fetchone() == (
                "fb.disposable-ledger.v1",
            )
        finally:
            con.close()
        with pytest.raises(DisposableLedgerError, match="different content"):
            ledger.append("evt-1", {"kind": "fill", "qty": 3})
        path = ledger.path
    assert not path.exists()


def test_disposable_ledger_refuses_repository_and_existing_paths(tmp_path: Path) -> None:
    with pytest.raises(DisposableLedgerError, match="overlap"):
        create_disposable_ledger(REPO, repo_root=REPO)
    existing = tmp_path / "financial-brain-disposable-ledger.duckdb"
    existing.write_bytes(b"preserve me")
    with pytest.raises(DisposableLedgerError, match="overwrite"):
        create_disposable_ledger(tmp_path, repo_root=REPO)
    assert existing.read_bytes() == b"preserve me"


def test_temporary_disposable_ledger_needs_no_credentials() -> None:
    with temporary_disposable_ledger(repo_root=REPO) as ledger:
        assert ledger.path.exists()
        assert ledger.append("offline", {"source": "fixture"})
