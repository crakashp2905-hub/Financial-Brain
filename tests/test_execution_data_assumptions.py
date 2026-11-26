"""Execution venue assumptions and data-rights fixture linkage."""
import json

import pytest

from scripts.validate_execution_data_assumptions import MANIFEST, validate


def test_execution_assumptions_and_fixture_rights_linkage_validate() -> None:
    validate()


@pytest.mark.parametrize("mutate", [
    lambda data: data["execution_assumptions"]["paper_exchange"].update(
        {"cancel_request": "CANCELS_IMMEDIATELY"}),
    lambda data: data["execution_assumptions"]["broker"].update(
        {"runtime_policy": "ENABLED"}),
    lambda data: data["execution_assumptions"]["multi_leg"].update(
        {"atomic_exchange_combo_order": "ASSUMED"}),
])
def test_manifest_rejects_unsafe_execution_assumptions(tmp_path, monkeypatch, mutate) -> None:
    import scripts.validate_execution_data_assumptions as validator

    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    mutate(data)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(validator, "MANIFEST", path)
    with pytest.raises(ValueError):
        validator.validate()
