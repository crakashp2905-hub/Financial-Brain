from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from financial_brain.config import Config
from financial_brain.paper.mf_allocation_policy import (
    MFAllocationPolicyError,
    MFOwnerAllocationPolicy,
    policy_as_of,
    record_policy,
)
from financial_brain.storage.db import Database


NOW = datetime(2026, 11, 24, 10, tzinfo=timezone.utc)


@pytest.fixture
def connection(tmp_path):
    database = Database(Config(data_root=tmp_path).ensure())
    database.migrate()
    with database.connect() as con:
        yield con


def policy(**changes):
    values = dict(
        policy_id="owner-default", owner_label="local-owner", version=1,
        effective_from=date(2026, 11, 1), effective_to=None,
        cash_reserve_fraction=Decimal("0.15"), max_equity_fraction=Decimal("0.50"),
        max_debt_fraction=Decimal("0.80"), max_etf_fraction=Decimal("0.20"),
        rebalance_band_fraction=Decimal("0.05"), sip_amount_inr=Decimal("5000"),
        sip_frequency="MONTHLY", sip_day=5,
    )
    values.update(changes)
    return MFOwnerAllocationPolicy(**values)


def test_owner_policy_is_durable_versioned_and_as_of_without_account_or_folio(connection):
    result = record_policy(connection, policy(), recorded_at=NOW)
    loaded = policy_as_of(connection, "owner-default", as_of=date(2026, 11, 24), known_at=NOW)

    assert result["status"] == "recorded"
    assert loaded["owner_label"] == "local-owner"
    assert loaded["sip_amount_inr"] == "5000"
    assert loaded["sip_frequency"] == "MONTHLY"
    assert loaded["cash_reserve_fraction"] == "0.15"
    assert loaded["mode"] == "PAPER_DECISION_ONLY"
    assert loaded["external_fund_transactions"] is False
    assert "account_id" not in loaded and "folio_id" not in loaded


def test_policy_recording_is_idempotent_but_same_version_mutation_is_rejected(connection):
    record_policy(connection, policy(), recorded_at=NOW)
    assert record_policy(connection, policy(), recorded_at=NOW)["status"] == "already_recorded"
    with pytest.raises(MFAllocationPolicyError, match="different settings"):
        record_policy(connection, policy(sip_amount_inr=Decimal("6000")), recorded_at=NOW)


def test_effective_versions_may_not_overlap_and_future_record_is_invisible(connection):
    record_policy(connection, policy(), recorded_at=NOW)
    next_version = policy(version=2, effective_from=date(2026, 12, 1))
    with pytest.raises(MFAllocationPolicyError, match="overlap"):
        record_policy(connection, next_version, recorded_at=NOW)
    assert policy_as_of(connection, "owner-default", as_of=date(2026, 10, 31), known_at=NOW) is None
    assert policy_as_of(
        connection, "owner-default", as_of=date(2026, 11, 24),
        known_at=datetime(2026, 11, 23, 10, tzinfo=timezone.utc),
    ) is None


@pytest.mark.parametrize("changes", [
    {"cash_reserve_fraction": Decimal("1.2")},
    {"sip_amount_inr": Decimal("0")},
    {"sip_frequency": None},
    {"sip_day": 32},
])
def test_invalid_owner_preferences_are_rejected(changes):
    with pytest.raises(MFAllocationPolicyError):
        policy(**changes)


def test_cli_exposes_policy_configuration_without_order_surface():
    from financial_brain.cli import main

    with pytest.raises(SystemExit) as error:
        main(["mf-policy", "--help"])
    assert error.value.code == 0
