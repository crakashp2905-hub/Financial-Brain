"""Separate, fully reconciled economic edge reports by asset class.

The report refuses an aggregate total unless every scheduled portfolio snapshot has
complete, hash-bound attribution for every supported asset class.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Mapping, Sequence


ASSET_CLASSES = ("CASH", "EQUITY", "OPTIONS", "FUTURES", "FX")


class AssetClassEdgeError(ValueError):
    """Invalid or internally inconsistent asset-class P&L attribution."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False,
                      ensure_ascii=False).encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def asset_class_snapshot_sha256(snapshot: Mapping[str, object]) -> str:
    unsigned = {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}
    return _sha(unsigned)


def _money(value: object, label: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise AssetClassEdgeError(f"{label} must be a finite decimal") from exc
    if not number.is_finite():
        raise AssetClassEdgeError(f"{label} must be a finite decimal")
    return number


def _iso_sessions(values: Sequence[str]) -> list[str]:
    try:
        dates = [date.fromisoformat(value).isoformat() for value in values]
    except (TypeError, ValueError) as exc:
        raise AssetClassEdgeError("scheduled sessions must be ISO calendar dates") from exc
    if not dates or dates != sorted(set(dates)):
        raise AssetClassEdgeError("scheduled sessions must be nonempty, unique and ordered")
    return dates


def _verified_account(archive: object, session_date: str) -> dict | None:
    if archive is None:
        return None
    if not isinstance(archive, dict):
        raise AssetClassEdgeError("account archive must be an object or missing")
    digest = archive.get("snapshot_sha256")
    if not isinstance(digest, str) or _sha({key: value for key, value in archive.items()
                                          if key != "snapshot_sha256"}) != digest:
        raise AssetClassEdgeError("account archive snapshot hash is invalid")
    if archive.get("session_date") != session_date:
        raise AssetClassEdgeError("account archive date differs from its report session")
    reconciliation = archive.get("return_reconciliation")
    required = {
        "economic_return_inr", "nav_return_inr", "realized_price_pnl_inr",
        "unrealized_price_pnl_inr", "gross_dividend_income_inr",
        "gross_cash_yield_income_inr", "fees_inr", "taxes_inr", "reconciled",
    }
    if (not isinstance(reconciliation, dict) or not required.issubset(reconciliation)
            or reconciliation.get("reconciled") is not True):
        raise AssetClassEdgeError("canonical account return reconciliation is incomplete")
    return reconciliation


def _verified_class_rows(rows: object) -> dict[str, dict[str, Decimal]] | None:
    if not isinstance(rows, list):
        return None
    by_class: dict[str, dict[str, Decimal]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            raise AssetClassEdgeError("asset-class attribution row must be an object")
        asset_class = raw.get("asset_class")
        if asset_class not in ASSET_CLASSES:
            raise AssetClassEdgeError(f"unsupported asset class: {asset_class}")
        if asset_class in by_class:
            raise AssetClassEdgeError(f"duplicate asset-class attribution: {asset_class}")
        digest = raw.get("snapshot_sha256")
        if not isinstance(digest, str) or asset_class_snapshot_sha256(raw) != digest:
            raise AssetClassEdgeError(f"{asset_class} attribution hash is invalid")
        if not isinstance(raw.get("source_id"), str) or not raw["source_id"].strip():
            raise AssetClassEdgeError(f"{asset_class} attribution source is required")
        measures = {
            key: _money(raw.get(key), f"{asset_class}.{key}")
            for key in ("gross_inr", "fees_inr", "taxes_inr", "other_costs_inr", "net_inr")
        }
        if any(measures[key] < 0 for key in ("fees_inr", "taxes_inr", "other_costs_inr")):
            raise AssetClassEdgeError(f"{asset_class} cumulative costs cannot be negative")
        if measures["net_inr"] != (measures["gross_inr"] - measures["fees_inr"]
                                    - measures["taxes_inr"] - measures["other_costs_inr"]):
            raise AssetClassEdgeError(f"{asset_class} net P&L does not reconcile to gross and costs")
        by_class[asset_class] = measures
    if set(by_class) != set(ASSET_CLASSES):
        return None
    return by_class


def build_asset_class_edge_report(*, scheduled_sessions: Sequence[str],
                                  observations: Sequence[dict]) -> dict:
    """Return class-specific cumulative P&L changes and block totals on attribution gaps.

    Each scheduled row carries one canonical `account_archive` and all five class snapshots.
    The class values are cumulative-to-date values, so daily contribution is reconstructed
    with adjacent snapshot differences. `other_costs_inr` holds financing and other
    economic costs not classified as fees or taxes.
    """
    dates = _iso_sessions(scheduled_sessions)
    by_date: dict[str, dict] = {}
    for row in observations:
        if not isinstance(row, dict):
            raise AssetClassEdgeError("each observation must be an object")
        session_date = row.get("session_date")
        if session_date not in dates:
            raise AssetClassEdgeError("observation contains an unscheduled date")
        if session_date in by_date:
            raise AssetClassEdgeError("duplicate portfolio session observation")
        by_date[session_date] = row

    metrics = ("gross_inr", "fees_inr", "taxes_inr", "other_costs_inr", "net_inr")
    totals = {name: {metric: Decimal(0) for metric in metrics} for name in ASSET_CLASSES}
    previous = {name: {metric: Decimal(0) for metric in metrics}
                for name in ASSET_CLASSES}
    complete_counts = {name: 0 for name in ASSET_CLASSES}
    class_gaps: dict[str, list[str]] = {name: [] for name in ASSET_CLASSES}
    rows_with_complete_attribution = 0
    portfolio_archive_gaps: list[str] = []
    aggregate_reconciled = True

    for session_date in dates:
        session_row = by_date.get(session_date)
        if session_row is None:
            portfolio_archive_gaps.append(session_date)
            for name in ASSET_CLASSES:
                class_gaps[name].append(session_date)
            aggregate_reconciled = False
            continue
        reconciliation = _verified_account(session_row.get("account_archive"), session_date)
        classes = _verified_class_rows(session_row.get("asset_class_attribution"))
        if reconciliation is None:
            portfolio_archive_gaps.append(session_date)
            aggregate_reconciled = False
        if classes is None:
            for name in ASSET_CLASSES:
                class_gaps[name].append(session_date)
            aggregate_reconciled = False
            continue
        rows_with_complete_attribution += 1
        for name in ASSET_CLASSES:
            measures = classes[name]
            if any(measures[key] < previous[name][key]
                   for key in ("fees_inr", "taxes_inr", "other_costs_inr")):
                raise AssetClassEdgeError(f"{name} cumulative costs cannot decrease")
            for metric in metrics:
                totals[name][metric] += measures[metric] - previous[name][metric]
            previous[name] = measures
            complete_counts[name] += 1
        if reconciliation is not None:
            account_totals = {
                "gross_inr": sum((_money(reconciliation[key], key) for key in (
                    "realized_price_pnl_inr", "unrealized_price_pnl_inr",
                    "gross_dividend_income_inr", "gross_cash_yield_income_inr")), Decimal(0)),
                "fees_inr": _money(reconciliation["fees_inr"], "fees_inr"),
                "taxes_inr": _money(reconciliation["taxes_inr"], "taxes_inr"),
                "net_inr": _money(reconciliation["economic_return_inr"], "economic_return_inr"),
            }
            class_totals = {
                key: sum((classes[name][key] for name in ASSET_CLASSES), Decimal(0))
                for key in account_totals
            }
            if any(class_totals[key] != account_totals[key] for key in account_totals):
                aggregate_reconciled = False
                portfolio_archive_gaps.append(f"{session_date}:ATTRIBUTION_MISMATCH")

    class_reports = []
    for name in ASSET_CLASSES:
        complete = complete_counts[name] == len(dates) and not class_gaps[name]
        class_reports.append({
            "asset_class": name,
            "status": "COMPLETE" if complete else "INCOMPLETE_ATTRIBUTION",
            "scheduled_denominator": len(dates),
            "observed_attribution_sessions": complete_counts[name],
            "missing_sessions": class_gaps[name],
            "gross_contribution_inr": str(totals[name]["gross_inr"]) if complete else None,
            "fees_inr": str(totals[name]["fees_inr"]) if complete else None,
            "taxes_inr": str(totals[name]["taxes_inr"]) if complete else None,
            "other_costs_inr": str(totals[name]["other_costs_inr"]) if complete else None,
            "net_contribution_inr": str(totals[name]["net_inr"]) if complete else None,
        })
    all_complete = (all(item["status"] == "COMPLETE" for item in class_reports)
                    and aggregate_reconciled)
    return {
        "schema_version": "1.0",
        "status": "RECONCILED_BY_ASSET_CLASS" if all_complete else "AGGREGATE_CLAIM_BLOCKED",
        "scheduled_denominator": len(dates),
        "observed_portfolio_sessions": len(by_date),
        "portfolio_archive_gaps": sorted(set(portfolio_archive_gaps)),
        "asset_classes": class_reports,
        "asset_class_attribution_sessions": rows_with_complete_attribution,
        "attribution_reconciled_to_canonical_account": aggregate_reconciled,
        "cross_asset_netting_used": False,
        "aggregate_net_contribution_inr": str(sum(
            (totals[name]["net_inr"] for name in ASSET_CLASSES), Decimal(0)))
            if all_complete else None,
        "aggregate_pnl_reportable": all_complete,
        "profitability_claim_eligible": False,
        "claim_scope": "ACCOUNTING_ATTRIBUTION_ONLY_NO_VALIDATED_ALPHA_OR_PROFITABILITY_CLAIM",
    }
