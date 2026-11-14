import hashlib
import json
from decimal import Decimal

import pytest

from financial_brain.forward_edge.asset_class_edge import (
    ASSET_CLASSES,
    AssetClassEdgeError,
    asset_class_snapshot_sha256,
    build_asset_class_edge_report,
)
from financial_brain.forward_edge.cli import cmd_asset_class_report


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def _class_row(name, gross, fees, *, taxes="0", other="0"):
    row = {
        "asset_class": name, "gross_inr": gross, "fees_inr": fees,
        "taxes_inr": taxes, "other_costs_inr": other,
        "net_inr": "0",
        "source_id": f"synthetic:{name.lower()}",
    }
    row["net_inr"] = str(Decimal(gross) - Decimal(fees) - Decimal(taxes) - Decimal(other))
    row["snapshot_sha256"] = asset_class_snapshot_sha256(row)
    return row


def _archive(day, gross, fees, net):
    row = {
        "account_id": "main-paper-account", "session_date": day,
        "return_reconciliation": {
            "economic_return_inr": str(net), "nav_return_inr": str(net),
            "realized_price_pnl_inr": str(gross), "unrealized_price_pnl_inr": "0",
            "gross_dividend_income_inr": "0", "gross_cash_yield_income_inr": "0",
            "fees_inr": str(fees), "taxes_inr": "0", "reconciled": True,
        },
    }
    row["snapshot_sha256"] = _hash(row)
    return row


def _observation(day, classes, gross, fees, net):
    return {
        "session_date": day,
        "account_archive": _archive(day, gross, fees, net),
        "asset_class_attribution": classes,
    }


def _classes(day):
    if day == "2026-11-12":
        values = {"EQUITY": ("100", "5"), "OPTIONS": ("-20", "2"),
                  "FUTURES": ("0", "0"), "FX": ("0", "0"), "CASH": ("0", "0")}
    else:
        values = {"EQUITY": ("300", "10"), "OPTIONS": ("-100", "5"),
                  "FUTURES": ("-40", "3"), "FX": ("0", "0"), "CASH": ("0", "0")}
    return [_class_row(name, gross, fees) for name, (gross, fees) in values.items()]


def _observations():
    return [
        _observation("2026-11-12", _classes("2026-11-12"), "80", "7", "73"),
        _observation("2026-11-13", _classes("2026-11-13"), "160", "18", "142"),
    ]


def test_separate_asset_class_contributions_reconcile_before_aggregate_is_shown():
    report = build_asset_class_edge_report(
        scheduled_sessions=["2026-11-12", "2026-11-13"], observations=_observations())
    by_class = {row["asset_class"]: row for row in report["asset_classes"]}
    assert set(by_class) == set(ASSET_CLASSES)
    assert by_class["EQUITY"]["net_contribution_inr"] == "290"
    assert by_class["OPTIONS"]["net_contribution_inr"] == "-105"
    assert by_class["FUTURES"]["net_contribution_inr"] == "-43"
    assert report["status"] == "RECONCILED_BY_ASSET_CLASS"
    assert report["aggregate_pnl_reportable"] is True
    assert report["profitability_claim_eligible"] is False
    assert report["cross_asset_netting_used"] is False
    assert report["aggregate_net_contribution_inr"] == "142"


def test_missing_class_observation_blocks_aggregate_instead_of_masking_losses():
    observations = _observations()
    observations[1]["asset_class_attribution"] = [
        row for row in observations[1]["asset_class_attribution"] if row["asset_class"] != "OPTIONS"]
    report = build_asset_class_edge_report(
        scheduled_sessions=["2026-11-12", "2026-11-13"], observations=observations)
    by_class = {row["asset_class"]: row for row in report["asset_classes"]}
    assert by_class["OPTIONS"]["status"] == "INCOMPLETE_ATTRIBUTION"
    assert report["aggregate_pnl_reportable"] is False
    assert report["aggregate_net_contribution_inr"] is None


def test_class_totals_must_reconcile_to_canonical_account_archive():
    observations = _observations()
    observations[1]["asset_class_attribution"][0] = _class_row("EQUITY", "301", "10")
    report = build_asset_class_edge_report(
        scheduled_sessions=["2026-11-12", "2026-11-13"], observations=observations)
    assert report["attribution_reconciled_to_canonical_account"] is False
    assert report["aggregate_pnl_reportable"] is False
    assert report["aggregate_net_contribution_inr"] is None


def test_duplicate_or_tampered_class_evidence_is_rejected():
    observations = _observations()
    observations[0]["asset_class_attribution"].append(observations[0]["asset_class_attribution"][0])
    with pytest.raises(AssetClassEdgeError, match="duplicate"):
        build_asset_class_edge_report(
            scheduled_sessions=["2026-11-12", "2026-11-13"], observations=observations)

    observations = _observations()
    observations[0]["asset_class_attribution"][0]["gross_inr"] = "999"
    with pytest.raises(AssetClassEdgeError, match="hash"):
        build_asset_class_edge_report(
            scheduled_sessions=["2026-11-12", "2026-11-13"], observations=observations)


def test_missing_portfolio_session_remains_in_denominator_and_blocks_claim():
    report = build_asset_class_edge_report(
        scheduled_sessions=["2026-11-12", "2026-11-13", "2026-11-16"],
        observations=_observations())
    assert report["scheduled_denominator"] == 3
    assert report["observed_portfolio_sessions"] == 2
    assert report["portfolio_archive_gaps"] == ["2026-11-16"]
    assert report["aggregate_pnl_reportable"] is False


def test_cli_emits_attribution_report_and_returns_incomplete_code_for_missing_sessions(tmp_path):
    path = tmp_path / "asset-class-evidence.json"
    payload = {
        "scheduled_sessions": ["2026-11-12", "2026-11-13", "2026-11-16"],
        "observations": _observations(),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    report, code = cmd_asset_class_report(str(path))
    assert code == 3
    assert report["aggregate_net_contribution_inr"] is None
    assert report["profitability_claim_eligible"] is False
