"""Frozen return-distribution and top-contributor sensitivity reporting."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
import hashlib
import json
import math

import numpy as np


class ContributionReportError(ValueError):
    """Invalid schedule, outcome attribution, or frozen analysis protocol."""


def _sha(value: str, label: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ContributionReportError(f"{label} must be a lowercase SHA-256 digest")


def _utc(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ContributionReportError(f"{label} must be a timezone-aware ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContributionReportError(f"{label} must be a timezone-aware ISO timestamp")
    return parsed.astimezone(timezone.utc)


def _schedule(values: Sequence[str]) -> tuple[str, ...]:
    try:
        parsed = tuple(date.fromisoformat(day).isoformat() for day in values)
    except (TypeError, ValueError) as exc:
        raise ContributionReportError("scheduled sessions must be ISO dates") from exc
    if not parsed or parsed != tuple(values) or parsed != tuple(sorted(set(parsed))):
        raise ContributionReportError("scheduled sessions must be nonempty, unique and ordered")
    return parsed


def _distribution(values: list[float]) -> dict:
    array = np.asarray(values, dtype=float)
    return {
        "observations": len(values),
        "mean": float(np.mean(array)) if values else None,
        "median": float(np.median(array)) if values else None,
        "standard_deviation": float(np.std(array, ddof=1)) if len(values) > 1 else None,
        "quantiles": ({str(q): float(np.quantile(array, q)) for q in (0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99)}
                      if values else {}),
        "minimum": min(values) if values else None,
        "maximum": max(values) if values else None,
        "positive_fraction": sum(value > 0 for value in values) / len(values) if values else None,
        "negative_fraction": sum(value < 0 for value in values) / len(values) if values else None,
    }


def contribution_report(
    *,
    scheduled_sessions: Sequence[str],
    daily_net_returns: Mapping[str, float | None],
    contributions: Sequence[Mapping[str, object]],
    protocol_sha256: str,
    frozen_at_utc: str,
    first_observation_at_utc: str,
    reconciliation_tolerance: float = 1e-8,
) -> dict:
    """Expose return distribution and fixed-denominator removal sensitivity.

    Contribution rows are net return contributions (including costs), attributed to
    one instrument and date. Their per-day totals must reconcile to observed daily
    returns. The protocol identity and freeze time are returned with the report.
    """
    sessions = _schedule(scheduled_sessions)
    _sha(protocol_sha256, "protocol_sha256")
    frozen = _utc(frozen_at_utc, "frozen_at_utc")
    first = _utc(first_observation_at_utc, "first_observation_at_utc")
    if frozen >= first:
        raise ContributionReportError("contribution protocol must be frozen before first observation")
    if not math.isfinite(reconciliation_tolerance) or reconciliation_tolerance < 0:
        raise ContributionReportError("reconciliation tolerance must be finite and nonnegative")
    extra = sorted(set(daily_net_returns) - set(sessions))
    if extra:
        raise ContributionReportError("daily returns contain unscheduled sessions")
    observed: dict[str, float] = {}
    missing: list[str] = []
    for day in sessions:
        value = daily_net_returns.get(day)
        if value is None:
            missing.append(day)
            continue
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ContributionReportError("daily net returns must be finite")
        observed[day] = numeric

    seen: set[str] = set()
    by_day: dict[str, float] = defaultdict(float)
    by_symbol: dict[str, float] = defaultdict(float)
    by_month: dict[str, float] = defaultdict(float)
    normalized = []
    for row in contributions:
        identity, day, symbol = row.get("contribution_id"), row.get("session"), row.get("instrument")
        if not isinstance(identity, str) or not identity.strip() or identity in seen:
            raise ContributionReportError("contribution_id must be present and unique")
        seen.add(identity)
        if not isinstance(day, str) or day not in sessions:
            raise ContributionReportError("contribution contains an unscheduled session")
        if day not in observed:
            raise ContributionReportError("contribution exists for a missing daily return")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ContributionReportError("instrument must be a nonempty label")
        try:
            value = float(row["net_return_contribution"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ContributionReportError("net return contribution must be numeric") from exc
        if not math.isfinite(value):
            raise ContributionReportError("net return contributions must be finite")
        by_day[day] += value
        by_symbol[symbol] += value
        by_month[day[:7]] += value
        normalized.append({"contribution_id": identity, "session": day,
                           "instrument": symbol, "net_return_contribution": value})

    for day, total in by_day.items():
        if abs(total - observed[day]) > reconciliation_tolerance:
            raise ContributionReportError(f"instrument contributions do not reconcile on {day}")
    # No attribution rows imply a zero contribution for a zero-return session only.
    for day, value in observed.items():
        if day not in by_day and abs(value) > reconciliation_tolerance:
            raise ContributionReportError(f"missing instrument attribution for nonzero session {day}")

    observed_values = [observed[day] for day in sessions if day in observed]
    observed_sum = sum(observed_values)
    denominator = len(sessions)

    def top(items: Mapping[str, float]) -> dict | None:
        if not items:
            return None
        name, value = min(items.items(), key=lambda item: (-item[1], item[0]))
        return {"key": name, "net_return_contribution": value}

    best_day, worst_day = top(by_day), None
    if by_day:
        name, value = min(by_day.items(), key=lambda item: (item[1], item[0]))
        worst_day = {"key": name, "net_return_contribution": value}
    best_month, worst_month = top(by_month), None
    if by_month:
        name, value = min(by_month.items(), key=lambda item: (item[1], item[0]))
        worst_month = {"key": name, "net_return_contribution": value}
    best_instrument, worst_instrument = top(by_symbol), None
    if by_symbol:
        name, value = min(by_symbol.items(), key=lambda item: (item[1], item[0]))
        worst_instrument = {"key": name, "net_return_contribution": value}

    removals = {}
    for group, candidate in (("best_day", best_day), ("best_month", best_month),
                             ("best_instrument", best_instrument)):
        contribution = 0.0 if candidate is None else float(candidate["net_return_contribution"])
        removals[group] = {
            "removed_key": None if candidate is None else candidate["key"],
            "removed_net_return_contribution": contribution,
            "remaining_sum_over_scheduled_sessions": observed_sum - contribution,
            "remaining_mean_over_scheduled_sessions": (observed_sum - contribution) / denominator,
        }
    canonical_rows = sorted(normalized, key=lambda x:x["contribution_id"])
    report_payload = {"schedule": sessions, "daily_net_returns": dict(sorted(observed.items())),
                      "contributions": canonical_rows, "protocol_sha256": protocol_sha256,
                      "frozen_at_utc": frozen.isoformat(),
                      "first_observation_at_utc": first.isoformat()}
    report_hash = hashlib.sha256(json.dumps(report_payload, sort_keys=True, separators=(",", ":"),
                                           allow_nan=False).encode()).hexdigest()
    return {
        "status": "INCOMPLETE" if missing else "COMPLETE",
        "report_sha256": report_hash,
        "protocol_sha256": protocol_sha256,
        "frozen_at_utc": frozen.isoformat(),
        "first_observation_at_utc": first.isoformat(),
        "scheduled_denominator": denominator,
        "observed_sessions": len(observed),
        "missing_sessions": missing,
        "observed_return_distribution": _distribution(observed_values),
        "distribution_scope": "observed sessions only; missing scheduled dates remain in denominator and withhold complete-cohort claims",
        "best_day": best_day,
        "worst_day": worst_day,
        "best_month": best_month,
        "worst_month": worst_month,
        "best_instrument": best_instrument,
        "worst_instrument": worst_instrument,
        "top_contributor_removal_sensitivity": removals,
        "one_day_contribution_share_of_positive_pnl": (
            max(0.0, max(by_day.values(), default=0.0)) / sum(x for x in by_day.values() if x > 0)
            if any(x > 0 for x in by_day.values()) else None
        ),
    }
