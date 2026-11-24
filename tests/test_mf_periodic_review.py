from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.config import Config
from financial_brain.paper.mf_allocation_policy import (
    MFOwnerAllocationPolicy, record_policy,
)
from financial_brain.paper.mf_lot_book import create_account
from financial_brain.paper.mf_periodic_review import (
    MFPeriodicReviewError, build_weekly_review,
)
from financial_brain.storage.db import Database


NOW = datetime(2026, 11, 24, 10, tzinfo=timezone.utc)


def _account(*, stale=False):
    return {
        "account_id": "paper-owner-1", "cash_inr": Decimal("180"),
        "pending_redemption_inr": Decimal("20"), "valuation_known_at": NOW,
        "positions": [
            {"scheme_code": "A", "plan": "DIRECT", "option": "GROWTH", "isin": "INA",
             "units": Decimal("7"), "market_value_inr": Decimal("700"),
             "unrealized_pnl_inr": Decimal("70"), "nav_date": NOW.date(),
             "nav_observation_id": "nav-a", "nav_available_at": NOW,
             "stale": stale, "valuation_status": "STALE" if stale else "CURRENT"},
            {"scheme_code": "B", "plan": "DIRECT", "option": "GROWTH", "isin": "INB",
             "units": Decimal("1"), "market_value_inr": Decimal("100"),
             "unrealized_pnl_inr": Decimal("-5"), "nav_date": NOW.date(),
             "nav_observation_id": "nav-b", "nav_available_at": NOW,
             "stale": False, "valuation_status": "CURRENT"},
        ],
    }


def _policy():
    return {"policy_id": "owner-policy", "version": 2, "policy_sha256": "hash",
            "cash_reserve_fraction": Decimal("0.10"),
            "rebalance_band_fraction": Decimal("0.05")}


def _sip(scheduled_for, state="SCHEDULED"):
    return {"installment_id": "sip-next", "plan_id": "monthly", "scheme_code": "A",
            "scheduled_for": scheduled_for, "amount_inr": Decimal("40"),
            "current_state": state}


def test_weekly_review_uses_explicit_targets_and_reserves_near_term_sips():
    result = build_weekly_review(
        account=_account(), policy=_policy(), target_weights={"A": "0.8", "B": "0.1"},
        installments=[_sip(NOW.date() + timedelta(days=5))], generated_at=NOW)

    assert result["rebalance_status"] == "READY_FOR_OWNER_REVIEW"
    assert [r["action"] for r in result["rebalance_candidates"]] == [
        "ADD_REVIEW", "HOLD_WITHIN_BAND"]
    assert result["totals"]["net_paper_assets_inr"] == "1000"
    assert result["totals"]["unrealized_pnl_inr"] == "65"
    assert result["totals"]["near_term_unpaid_sip_reserve_inr"] == "40"
    assert result["liquidity_candidate"]["candidate_amount_inr"] == "40.00"
    assert result["upcoming_sips"][0]["state"] == "SCHEDULED"
    assert result["report_sha256"]


def test_stale_or_missing_nav_blocks_rebalance_and_liquidity_suggestions():
    account = _account(stale=True)
    result = build_weekly_review(account=account, policy=_policy(),
                                 target_weights={"A": "0.8", "B": "0.1"},
                                 installments=[], generated_at=NOW)

    assert result["rebalance_status"] == "BLOCKED_DATA_OR_TARGET_GAP"
    assert result["rebalance_candidates"] == []
    assert result["totals"]["net_paper_assets_inr"] is None
    assert result["liquidity_candidate"]["status"] == "BLOCKED_DATA_GAP"
    assert result["rebalance_gaps"]["nav"][0]["scheme_code"] == "A"


def test_unmapped_holding_blocks_rebalance_and_sip_reserve_excludes_cleared_funds():
    result = build_weekly_review(
        account=_account(), policy=_policy(), target_weights={"A": "0.9"},
        installments=[_sip(NOW.date() + timedelta(days=5), "FUNDS_AVAILABLE")],
        generated_at=NOW)

    assert result["rebalance_status"] == "BLOCKED_DATA_OR_TARGET_GAP"
    assert result["rebalance_gaps"]["target_map_missing_schemes"] == ["B"]
    assert result["totals"]["near_term_unpaid_sip_reserve_inr"] == "0"


@pytest.mark.parametrize("targets", [
    {"A": "0.91"}, {"A": "NaN"}, {},
])
def test_invalid_or_overallocated_targets_are_rejected(targets):
    with pytest.raises(MFPeriodicReviewError):
        build_weekly_review(account=_account(), policy=_policy(), target_weights=targets,
                            installments=[], generated_at=NOW)


def test_nonpositive_or_naive_review_clocks_are_rejected():
    with pytest.raises(MFPeriodicReviewError, match="timezone"):
        build_weekly_review(account=_account(), policy=_policy(),
                            target_weights={"A": "0.8", "B": "0.1"}, installments=[],
                            generated_at=NOW.replace(tzinfo=None))
    with pytest.raises(MFPeriodicReviewError, match="horizon"):
        build_weekly_review(account=_account(), policy=_policy(),
                            target_weights={"A": "0.8", "B": "0.1"}, installments=[],
                            generated_at=NOW, sip_horizon_days=500)


def test_mf_review_cli_reads_paper_book_and_writes_hashed_report(tmp_path, monkeypatch):
    import json

    from financial_brain import cli

    config = Config(data_root=tmp_path).ensure()
    database = Database(config)
    database.migrate()
    with database.connect() as con:
        create_account(con, "cli-account", 10000, now=NOW)
        record_policy(con, MFOwnerAllocationPolicy(
            policy_id="cli-policy", owner_label="owner", version=1,
            effective_from=NOW.date() - timedelta(days=10), effective_to=None,
            cash_reserve_fraction=Decimal("0.15"), max_equity_fraction=Decimal("0.5"),
            max_debt_fraction=Decimal("0.8"), max_etf_fraction=Decimal("0.2"),
            rebalance_band_fraction=Decimal("0.05")), recorded_at=NOW)
    targets = tmp_path / "targets.json"
    targets.write_text(json.dumps({"target_weights": {"SCHEME-A": "0.85"}}),
                       encoding="utf-8")
    output = tmp_path / "weekly-review.json"
    monkeypatch.setattr(cli, "load", lambda: config)

    result = cli.main(["mf-review", "--account-id", "cli-account", "--policy-id", "cli-policy",
                       "--targets", str(targets), "--as-of", NOW.isoformat(),
                       "--output", str(output)])

    report = json.loads(output.read_text(encoding="utf-8"))
    assert result == 0
    assert report["rebalance_status"] == "READY_FOR_OWNER_REVIEW"
    assert report["rebalance_candidates"][0]["action"] == "ADD_REVIEW"
    assert report["report_sha256"]
    assert report["limitations"][0].endswith("no broker or fund order is created.")
