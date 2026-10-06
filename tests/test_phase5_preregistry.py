"""The pre-registration audit: written before the result, never changed after it - read from git."""
import subprocess
from pathlib import Path

import pytest

from financial_brain.research import preregistry as PR


def git(repo, *a, env=None):
    subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, env=env)


def commit(repo, msg, when):
    import os
    env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    git(repo, "add", "-A", env=env)
    git(repo, "commit", "-q", "-m", msg, env=env)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / PR.RESEARCH).mkdir(parents=True)
    return tmp_path


def write(repo, name, text):
    (repo / PR.RESEARCH / name).write_text(text, encoding="utf-8")


def status(repo, name):
    return next(r["status"] for r in PR.audit(repo) if r["prereg"] == name)


def test_registered_then_resulted_then_untouched_is_ok(repo):
    write(repo, "PRE-REGISTERED a.md", "claim A")
    commit(repo, "reg", "2026-01-01T10:00:00+05:30")
    write(repo, "Result of a.md", "see [[PRE-REGISTERED a]]")
    commit(repo, "res", "2026-01-02T10:00:00+05:30")
    assert status(repo, "PRE-REGISTERED a.md") == "OK"


def test_editing_the_hypothesis_after_its_result_is_a_violation(repo):
    write(repo, "PRE-REGISTERED a.md", "claim A")
    commit(repo, "reg", "2026-01-01T10:00:00+05:30")
    write(repo, "Result of a.md", "see [[PRE-REGISTERED a]]")
    commit(repo, "res", "2026-01-02T10:00:00+05:30")
    write(repo, "PRE-REGISTERED a.md", "claim A, quietly weakened")
    commit(repo, "edit", "2026-01-03T10:00:00+05:30")
    assert status(repo, "PRE-REGISTERED a.md") == "CHANGED_AFTER_RESULT"


def test_an_amendment_before_any_result_is_allowed_and_labelled(repo):
    write(repo, "PRE-REGISTERED a.md", "claim A")
    commit(repo, "reg", "2026-01-01T10:00:00+05:30")
    write(repo, "PRE-REGISTERED a.md", "claim A\n\n## Amendment, before anything was computed")
    commit(repo, "amend", "2026-01-01T11:00:00+05:30")
    write(repo, "Result of a.md", "see [[PRE-REGISTERED a]]")
    commit(repo, "res", "2026-01-02T10:00:00+05:30")
    assert status(repo, "PRE-REGISTERED a.md") == "AMENDED_BEFORE_RESULT"


def test_a_result_older_than_its_hypothesis_is_caught(repo):
    write(repo, "Result of a.md", "see [[PRE-REGISTERED a]]")
    commit(repo, "res", "2026-01-01T10:00:00+05:30")
    write(repo, "PRE-REGISTERED a.md", "claim A")
    commit(repo, "reg", "2026-01-02T10:00:00+05:30")
    assert status(repo, "PRE-REGISTERED a.md") == "RESULT_BEFORE_REGISTRATION"


def test_this_repository_has_no_violations():
    root = Path(__file__).resolve().parents[1]
    bad = [r["prereg"] for r in PR.audit(root) if r["status"] in PR.VIOLATIONS]
    assert bad == []
