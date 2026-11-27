"""Release-blocking gate for frozen prospective paper evidence.

The gate reports evidence readiness only. It never grants broker access or authorizes
capital. Every input is expected to be a source-hashed, independently reviewable record.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from typing import Any

from financial_brain.forward_edge.custody import (
    CustodyStatus,
    Receipt,
    TrustRoot,
    classify_custody,
)

class ForwardEvidenceGateError(ValueError):
    """Malformed preregistration or prospective evidence input."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _sha(value: Any, field: str) -> str | None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        return None
    return value


def _report_digest_valid(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    expected = _sha(value.get("report_sha256"), "report_sha256")
    return expected is not None and expected == _digest(
        {key: item for key, item in value.items() if key != "report_sha256"})


def evaluate_forward_evidence_gate(*, plan: dict[str, Any] | None,
                                   sessions: list[dict[str, Any]] | None,
                                   attribution: dict[str, Any] | None,
                                   benchmark: dict[str, Any] | None) -> dict[str, Any]:
    """Return RED until the complete frozen session, attribution and benchmark scope is met."""
    checks: dict[str, bool] = {
        "frozen_plan_valid": False,
        "all_expected_sessions_present_and_clean": False,
        "all_sessions_independently_attested": False,
        "attribution_complete_and_reconciled": False,
        "benchmark_superiority_observed": False,
    }
    issues: list[str] = []
    expected_dates: list[str] = []
    benchmark_id: str | None = None
    if not isinstance(plan, dict):
        issues.append("frozen evidence-window plan is missing")
    else:
        plan_body = {key: value for key, value in plan.items() if key != "plan_sha256"}
        frozen = _timestamp(plan.get("frozen_at_utc"))
        try:
            first_session = date.fromisoformat(plan["first_eligible_session"])
            raw_dates = plan["expected_sessions"]
            if not isinstance(raw_dates, list) or not raw_dates:
                raise ValueError("expected_sessions must be a non-empty list")
            parsed_dates = [date.fromisoformat(item) for item in raw_dates]
            minimum = plan["minimum_sessions"]
            benchmark_id = plan["benchmark_id"]
            strategy_sha256 = plan["strategy_sha256"]
            cohort_id = plan["cohort_id"]
            calendar_sha256 = plan["market_calendar_sha256"]
            raw_roots = plan["trusted_custody_roots"]
            if not isinstance(raw_roots, list) or not raw_roots:
                raise ValueError("trusted_custody_roots must be non-empty")
            trust_roots = {}
            for root in raw_roots:
                if not isinstance(root, dict):
                    raise ValueError("custody trust root must be an object")
                provider_id, key_id, public_key_b64 = (
                    root.get("provider_id"), root.get("key_id"), root.get("public_key_b64"))
                if not all(isinstance(value, str) and value.strip()
                           for value in (provider_id, key_id, public_key_b64)):
                    raise ValueError("custody trust root identity and key are required")
                identity = (provider_id, key_id)
                if identity in trust_roots:
                    raise ValueError("duplicate custody trust root")
                trust_roots[identity] = TrustRoot(provider_id, key_id, public_key_b64)
            dates_valid = (parsed_dates == sorted(set(parsed_dates))
                           and len(parsed_dates) >= 60
                           and type(minimum) is int and minimum >= 60
                           and len(parsed_dates) >= minimum
                           and parsed_dates[0] >= first_session)
            freeze_valid = (frozen is not None and frozen.date() < first_session
                            and all(frozen.date() < item for item in parsed_dates))
            plan_valid = (dates_valid and freeze_valid and isinstance(benchmark_id, str)
                          and bool(benchmark_id.strip())
                          and isinstance(cohort_id, str) and bool(cohort_id.strip())
                          and _sha(strategy_sha256, "strategy_sha256") is not None
                          and _sha(calendar_sha256, "market_calendar_sha256") is not None
                          and _sha(plan.get("plan_sha256"), "plan_sha256") == _digest(plan_body))
            if not plan_valid:
                issues.append("frozen plan hash, date order, freeze time, sample floor or benchmark is invalid")
            else:
                checks["frozen_plan_valid"] = True
                expected_dates = [item.isoformat() for item in parsed_dates]
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            issues.append(f"frozen evidence-window plan is malformed: {exc}")

    expected_set = set(expected_dates)
    observed: dict[str, dict[str, Any]] = {}
    session_rows = sessions if isinstance(sessions, list) else []
    for row in session_rows:
        if not isinstance(row, dict) or not isinstance(row.get("session_date"), str):
            issues.append("malformed session evidence row")
            continue
        session_date = row["session_date"]
        if session_date in observed:
            issues.append(f"duplicate session evidence for {session_date}")
        observed[session_date] = row
    if checks["frozen_plan_valid"]:
        missing = sorted(expected_set - set(observed))
        extra = sorted(set(observed) - expected_set)
        dirty = sorted(day for day, row in observed.items()
                       if day in expected_set and row.get("clean_session") is not True)
        valid_raw = all(_sha(observed[day].get("raw_audit_sha256"), "raw_audit_sha256")
                        is not None for day in expected_dates if day in observed)
        custody_valid = True
        for day in expected_dates:
            row = observed.get(day)
            if row is None:
                custody_valid = False
                continue
            receipt_data = row.get("custody_receipt")
            decision_at = _timestamp(row.get("decision_at_utc"))
            entry_at = _timestamp(row.get("entry_earliest_at_utc"))
            try:
                receipt = Receipt(
                    provider_id=receipt_data["provider_id"], key_id=receipt_data["key_id"],
                    digest_sha256=receipt_data["digest_sha256"],
                    anchored_at_utc=_timestamp(receipt_data["anchored_at_utc"]),
                    signature_b64=receipt_data["signature_b64"])
                custody_status = (classify_custody(
                    manifest_sha256=row["raw_audit_sha256"], decision_at_utc=decision_at,
                    entry_earliest_at_utc=entry_at, receipt=receipt, trust_roots=trust_roots)
                    if decision_at is not None and entry_at is not None else CustodyStatus.CONFLICTED)
            except (KeyError, TypeError, ValueError, AttributeError):
                custody_status = CustodyStatus.UNVERIFIED
            if custody_status is not CustodyStatus.VERIFIED:
                custody_valid = False
        checks["all_expected_sessions_present_and_clean"] = not missing and not extra and not dirty
        checks["all_sessions_independently_attested"] = (
            not missing and valid_raw and custody_valid and all(
                observed[day].get("strategy_sha256") == strategy_sha256
                and observed[day].get("cohort_id") == cohort_id
                and observed[day].get("evidence_tier") == "ATTESTED_FORWARD"
                for day in expected_dates if day in observed))
        if missing:
            issues.append(f"expected future sessions are missing: {missing[:10]}")
        if extra:
            issues.append(f"unplanned session observations are present: {extra[:10]}")
        if dirty:
            issues.append(f"sessions are not clean/reconciled: {dirty[:10]}")
        if not valid_raw or not checks["all_sessions_independently_attested"]:
            issues.append("every expected session requires a raw-audit digest and valid pinned Ed25519 custody receipt")
    else:
        issues.append("session evidence cannot qualify without a valid frozen plan")

    if isinstance(attribution, dict) and expected_dates:
        attr_dates = attribution.get("session_dates")
        checks["attribution_complete_and_reconciled"] = (
            attr_dates == expected_dates
            and attribution.get("strategy_sha256") == strategy_sha256
            and attribution.get("cohort_id") == cohort_id
            and attribution.get("complete") is True
            and attribution.get("ledger_reconciled") is True
            and _report_digest_valid(attribution)
        )
    if not checks["attribution_complete_and_reconciled"]:
        issues.append("complete ledger-reconciled attribution for the exact frozen window is missing")

    if isinstance(benchmark, dict) and expected_dates:
        benchmark_dates = benchmark.get("session_dates")
        lower_cash = _finite_number(benchmark.get("cash_alpha_lower_bound"))
        lower_risk_matched = _finite_number(benchmark.get("risk_matched_alpha_lower_bound"))
        checks["benchmark_superiority_observed"] = (
            benchmark.get("benchmark_id") == benchmark_id
            and benchmark.get("strategy_sha256") == strategy_sha256
            and benchmark.get("cohort_id") == cohort_id
            and benchmark_dates == expected_dates
            and lower_cash is not None and lower_cash > 0
            and lower_risk_matched is not None and lower_risk_matched > 0
            and benchmark.get("independent_receipts_verified") is True
            and benchmark.get("evidence_tier") == "INDEPENDENT_FORWARD"
            and _report_digest_valid(benchmark)
        )
    if not checks["benchmark_superiority_observed"]:
        issues.append("positive independent-forward lower bounds against cash and risk-matched benchmark are missing")

    ready = all(checks.values())
    body = {
        "schema": "fb.forward-evidence-release-gate.v1",
        "status": "GREEN_REVIEW_REQUIRED" if ready else "RED",
        "release_blocked": not ready,
        "trading_authorized": False,
        "promotion_allowed": False,
        "benchmark_id": benchmark_id,
        "expected_session_count": len(expected_dates),
        "observed_expected_session_count": sum(day in observed for day in expected_dates),
        "checks": checks,
        "issues": issues,
    }
    return body | {"report_sha256": _digest(body)}


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    import math
    return float(value) if math.isfinite(value) else None
