"""Protocol deviation records are version-bound and cannot hide evidence downgrades."""
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.validate_deviation_log import DeviationLogError, load, validate


ROOT = Path(__file__).resolve().parents[1]


def test_known_deviation_register_is_bound_to_protocol_commit_and_evidence() -> None:
    document = load(ROOT)
    assert len(document["records"]) == 10
    assert document["audit_coverage"] == "PARTIAL_KNOWN_DEVIATIONS"
    assert all("NO_UPGRADE" in row["effect_on_evidence_status"] for row in document["records"])


@pytest.mark.parametrize("mutation, message", [
    ("duplicate", "unique"),
    ("rollback", "rollback"),
    ("protocol_hash", "SHA-256"),
    ("coverage", "exhaustive"),
])
def test_deviation_register_rejects_incomplete_or_misleading_claims(mutation: str, message: str) -> None:
    document = deepcopy(load(ROOT))
    if mutation == "duplicate":
        document["records"].append(deepcopy(document["records"][0]))
    elif mutation == "rollback":
        document["records"][0]["rollback"] = ""
    elif mutation == "protocol_hash":
        document["protocol"]["sha256"] = "0" * 64
    elif mutation == "coverage":
        document["audit_coverage"] = "COMPLETE"
    with pytest.raises(DeviationLogError, match=message):
        validate(document, ROOT)
