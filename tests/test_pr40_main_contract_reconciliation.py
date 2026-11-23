import json

import pytest

from scripts.validate_pr40_main_contract_reconciliation import CONTRACTS, REPORT, validate


def test_reconciliation_covers_all_public_contracts_with_exact_source_blobs():
    validate(REPORT.read_text(encoding="utf-8"),
             json.loads(CONTRACTS.read_text(encoding="utf-8")))


def test_reconciliation_rejects_missing_or_reclassified_contract():
    report = REPORT.read_text(encoding="utf-8")
    contracts = json.loads(CONTRACTS.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="every canonical public contract"):
        validate(report.replace("| `operator_cli` |", "| `missing_cli` |"), contracts)
    with pytest.raises(ValueError, match="unexpected reconciliation action"):
        validate(report.replace("| **KEEP** |", "| **REJECT** |", 1), contracts)
