"""Disposable local DuckDB ledgers (FE-0009): scratch databases that cannot be, or overwrite, real research data."""
from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb

PROTECTED_NAMES = frozenset({"financial_brain.duckdb", "fo_history.duckdb"})
PROTECTED_DIRS = ("data", "docs", "brain")


class ProtectedPathError(PermissionError):
    pass


def assert_not_protected(path: Path, repo_root: Path | None = None) -> None:
    p = Path(path).resolve()
    if p.name in PROTECTED_NAMES or p.name.startswith("financial_brain.duckdb"):
        raise ProtectedPathError(f"{p.name} is a real research or portfolio database")
    if repo_root is not None:
        root = Path(repo_root).resolve()
        for d in PROTECTED_DIRS:
            protected = (root / d)
            if protected == p or protected in p.parents:
                raise ProtectedPathError(f"{p} is inside protected directory {d}/")


@contextmanager
def disposable_duckdb(repo_root: Path | None = None) -> Iterator[tuple[duckdb.DuckDBPyConnection, Path]]:
    """A fresh temp-directory database, deleted on exit. Needs no credentials and touches no user data."""
    tmp = Path(tempfile.mkdtemp(prefix="fb-disposable-"))
    path = tmp / "scratch.duckdb"
    assert_not_protected(path, repo_root)
    con = duckdb.connect(str(path))
    try:
        yield con, path
    finally:
        con.close()
        shutil.rmtree(tmp, ignore_errors=True)


def open_for_write(path: Path, repo_root: Path) -> duckdb.DuckDBPyConnection:
    """The only sanctioned way to open a ledger for writing from forward-edge code: protected paths are refused."""
    assert_not_protected(path, repo_root)
    return duckdb.connect(str(path))
