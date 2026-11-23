from financial_brain.forward_edge.provenance import repository_provenance


def test_provenance_records_commit_branch_ancestry_and_entry_point(tmp_path):
    import subprocess

    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=Test", "-c",
                    "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "base"],
                   check=True, capture_output=True)
    result = repository_provenance(tmp_path, entry_point="fb test-session")
    assert result["source_commit"]
    assert result["source_branch"]
    assert len(result["branch_ancestry_sha256"]) == 64
    assert result["source_tree_state"] == "CLEAN"
    assert result["executable_entry_point"] == "fb test-session"
