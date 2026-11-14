from datetime import date
from decimal import Decimal
import json
from pathlib import Path

from financial_brain.kernel.india_options_expiry import (
    DeliveryMarginEvidence,
    preflight_india_expiry,
    rulebook_sha256,
    rules_as_dicts,
)
from financial_brain.kernel.options_paper import OptionContract, OptionsPaperAccount
from financial_brain.kernel.options_paper import OptionsPaperError


EXPIRY = date(2026, 11, 27)
HASH_A, HASH_B = "a" * 64, "b" * 64


def _contract(contract_id="NIFTY-C100", *, kind="CALL", strike="100",
              settlement="PHYSICAL", underlying_kind="STOCK", expiry=EXPIRY):
    return OptionContract(
        contract_id, "NIFTY", kind, Decimal(strike), expiry, 10, settlement,
        Decimal("500"), "fixture-contract-master", HASH_A,
        market="NFO", underlying_kind=underlying_kind,
    )


def _margin(contract_id, amount="500"):
    return DeliveryMarginEvidence(contract_id, Decimal(amount), "fixture-margin", HASH_B)


def test_physical_stock_expiry_rejects_cash_settlement_contract_terms(tmp_path):
    account = OptionsPaperAccount(tmp_path / "mismatch.jsonl", account_id="a",
                                  initial_cash=Decimal("10000"))
    contract = _contract(settlement="CASH")
    account.trade("open", contract, quantity_lots=1, premium=Decimal("0"),
                  session_date=date(2026, 11, 13), source_sha256=HASH_B)
    report = preflight_india_expiry(
        account, settlement_prices={"NIFTY": Decimal("120")}, session_date=EXPIRY,
        settlement_source_sha256=HASH_B, delivery_margins={contract.contract_id: _margin(contract.contract_id)},
    )
    assert report.status == "BLOCKED"
    assert f"CONTRACT_RULE_MISMATCH:{contract.contract_id}" in report.blockers


def test_expiry_requires_delivery_margin_and_sufficient_cash(tmp_path):
    account = OptionsPaperAccount(tmp_path / "cash.jsonl", account_id="a",
                                  initial_cash=Decimal("1000"))
    contract = _contract()
    account.trade("open", contract, quantity_lots=1, premium=Decimal("0"),
                  session_date=date(2026, 11, 13), source_sha256=HASH_B)
    no_margin = preflight_india_expiry(
        account, settlement_prices={"NIFTY": Decimal("120")}, session_date=EXPIRY,
        settlement_source_sha256=HASH_B,
    )
    assert f"MISSING_DELIVERY_MARGIN:{contract.contract_id}" in no_margin.blockers
    low_cash = preflight_india_expiry(
        account, settlement_prices={"NIFTY": Decimal("120")}, session_date=EXPIRY,
        settlement_source_sha256=HASH_B,
        delivery_margins={contract.contract_id: _margin(contract.contract_id, "500")},
    )
    assert "INSUFFICIENT_EXPIRY_CASH_AND_DELIVERY_MARGIN" in low_cash.blockers


def test_physical_delivery_requires_existing_shares_for_delivery_side(tmp_path):
    account = OptionsPaperAccount(tmp_path / "inventory.jsonl", account_id="a",
                                  initial_cash=Decimal("10000"))
    contract = _contract("NIFTY-P120", kind="PUT", strike="120")
    account.trade("open", contract, quantity_lots=1, premium=Decimal("1"),
                  session_date=date(2026, 11, 13), source_sha256=HASH_B)
    report = preflight_india_expiry(
        account, settlement_prices={"NIFTY": Decimal("100")}, session_date=EXPIRY,
        settlement_source_sha256=HASH_B,
        delivery_margins={contract.contract_id: _margin(contract.contract_id)},
    )
    assert "INSUFFICIENT_DELIVERY_INVENTORY:NIFTY" in report.blockers
    assert report.physical_share_deltas == (("NIFTY", -10),)


def test_expiry_rulebook_is_versioned_and_unsupported_legacy_stock_expiry_blocks(tmp_path):
    assert len(rulebook_sha256()) == 64
    account = OptionsPaperAccount(tmp_path / "legacy.jsonl", account_id="a",
                                  initial_cash=Decimal("10000"))
    old = _contract("OLD-C100", expiry=date(2019, 9, 26))
    account.trade("open", old, quantity_lots=1, premium=Decimal("0"),
                  session_date=date(2019, 9, 1), source_sha256=HASH_B)
    report = preflight_india_expiry(
        account, settlement_prices={"NIFTY": Decimal("120")},
        session_date=date(2019, 9, 26), settlement_source_sha256=HASH_B,
        delivery_margins={old.contract_id: _margin(old.contract_id)},
    )
    assert f"NO_EFFECTIVE_RULE:{old.contract_id}" in report.blockers


def test_machine_readable_expiry_rules_match_runtime_rulebook():
    table_path = Path(__file__).parents[1] / "docs/edge-program/india-options-expiry-rules.v1.json"
    table = json.loads(table_path.read_text(encoding="utf-8"))
    assert table["rulebook_sha256"] == rulebook_sha256()
    runtime = rules_as_dicts()
    documented = [{
        "rule_id": row["rule_id"], "venue": row["venue"],
        "underlying_kind": row["underlying_kind"],
        "effective_from": row["effective_from"], "settlement": row["settlement"],
        "exercise_style": row["exercise_style"], "source_refs": row["sources"],
    } for row in table["rules"]]
    assert documented == runtime


def test_preflight_cannot_be_reused_after_position_changes(tmp_path):
    account = OptionsPaperAccount(tmp_path / "stale.jsonl", account_id="a",
                                  initial_cash=Decimal("10000"))
    contract = _contract()
    account.trade("open-1", contract, quantity_lots=1, premium=Decimal("0"),
                  session_date=date(2026, 11, 13), source_sha256=HASH_B)
    readiness = account.preflight_india_expiry(
        settlement_prices={"NIFTY": Decimal("120")}, session_date=EXPIRY,
        settlement_source_sha256=HASH_B,
        delivery_margins={contract.contract_id: _margin(contract.contract_id)},
    )
    assert readiness.status == "READY"
    account.trade("open-2", contract, quantity_lots=1, premium=Decimal("0"),
                  session_date=date(2026, 11, 16), source_sha256=HASH_B)
    try:
        account.settle_expiry("stale-preflight", settlement_prices={"NIFTY": Decimal("120")},
                              session_date=EXPIRY, source_sha256=HASH_B,
                              readiness=readiness)
    except OptionsPaperError as exc:
        assert "preflight" in str(exc)
    else:
        raise AssertionError("stale expiry readiness must not authorize settlement")
