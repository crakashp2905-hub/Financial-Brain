import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_pins_are_explicit_and_offline_install_gap_stays_visible():
    contract = json.loads((ROOT / "docs/edge-program/reproducible-runtime.json").read_text(encoding="utf-8"))
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert (ROOT / contract["python_version_file"]).read_text(encoding="utf-8").strip() == contract["python_version"]
    assert contract["ci_installs_pinned_python"] is True
    assert contract["python_hash_seed"] == "0"
    assert "Per-experiment seeds" in contract["stochastic_seed_policy"]
    assert contract["market_timezone"] == "Asia/Kolkata"
    assert contract["ci_sets_hash_seed_and_timezone"] is True
    assert contract["dependency_lock_contains_exact_versions"] is True
    assert contract["ci_installs_from_dependency_lock"] is False
    assert contract["offline_clean_install_verified_on"] == []
    assert "python-version-file: \".python-version\"" in workflow
    assert "PYTHONHASHSEED: \"0\"" in workflow
    assert "TZ: Asia/Kolkata" in workflow
    assert (ROOT / "requirements-lock.txt").is_file()
    assert all((ROOT / path).is_file() for path in contract["evidence_sources"])


def test_deviation_log_records_author_clock_effect_and_rollback_for_every_deviation():
    log = json.loads((ROOT / "docs/edge-program/deviation-log.json").read_text(encoding="utf-8"))
    rows = log["deviations"]
    assert rows
    assert len({row["id"] for row in rows}) == len(rows)
    for row in rows:
        assert row["author"]
        assert row["clock_utc"].endswith("Z")
        assert row["version"] if "version" in row else log["version"]
        assert row["evidence_effect"]
        assert row["rollback"]
