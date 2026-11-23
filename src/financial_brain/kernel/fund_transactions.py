"""Source-bound, scheme-specific transaction cutoffs and NAV-date assignment rules."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum
from typing import Mapping

from financial_brain.kernel.trading_clock import (
    IST,
    BusinessCalendar,
    CalendarDomain,
    TradingClockError,
)


class FundTransactionError(ValueError):
    """A transaction cannot be assigned a NAV date from its sourced scheme policy."""


class TransactionKind(StrEnum):
    PURCHASE = "PURCHASE"
    REDEMPTION = "REDEMPTION"
    SWITCH_IN = "SWITCH_IN"
    SWITCH_OUT = "SWITCH_OUT"


@dataclass(frozen=True)
class SchemeTransactionRule:
    """Issuer-notice rule, effective and published dates included for no-lookahead use."""

    scheme_id: str
    transaction_kind: TransactionKind
    effective_from: date
    effective_to: date | None
    cutoff_local: time
    before_cutoff_lag: int
    after_cutoff_lag: int
    closed_day_lag: int
    published_at: datetime
    source_ref: str
    source_sha256: str
    timezone_name: str = "Asia/Kolkata"

    def __post_init__(self) -> None:
        if not self.scheme_id.strip() or not self.source_ref.strip():
            raise FundTransactionError("scheme id and source reference are required")
        object.__setattr__(self, "transaction_kind", TransactionKind(self.transaction_kind))
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise FundTransactionError("transaction-rule effective_to precedes effective_from")
        if any(type(value) is not int or value < 0 for value in
               (self.before_cutoff_lag, self.after_cutoff_lag, self.closed_day_lag)):
            raise FundTransactionError("NAV business-day lags must be non-negative integers")
        if self.timezone_name != "Asia/Kolkata":
            raise FundTransactionError("Indian scheme transaction rules must use Asia/Kolkata")
        if self.published_at.tzinfo is None or self.published_at.utcoffset() is None:
            raise FundTransactionError("rule published_at must be timezone aware")
        if len(self.source_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.source_sha256):
            raise FundTransactionError("source_sha256 must be lowercase SHA-256")


class SchemeTransactionCalendar:
    """Assign business dates from explicit fund/action-specific rules; no generic cutoff."""

    def __init__(self, calendars: Mapping[str, BusinessCalendar],
                 rules: tuple[SchemeTransactionRule, ...]) -> None:
        self.calendars = dict(calendars)
        self.rules = tuple(rules)
        for scheme_id, calendar in self.calendars.items():
            if calendar.domain is not CalendarDomain.SCHEME or calendar.calendar_id != scheme_id:
                raise FundTransactionError("scheme calendar key/domain mismatch")
        grouped: dict[tuple[str, TransactionKind], list[SchemeTransactionRule]] = {}
        for rule in self.rules:
            if rule.scheme_id not in self.calendars:
                raise FundTransactionError(f"no business calendar for scheme {rule.scheme_id}")
            grouped.setdefault((rule.scheme_id, rule.transaction_kind), []).append(rule)
        for key, versions in grouped.items():
            versions.sort(key=lambda rule: rule.effective_from)
            for prior, following in zip(versions, versions[1:]):
                if prior.effective_to is None or prior.effective_to >= following.effective_from:
                    raise FundTransactionError(f"overlapping effective rules for {key[0]} {key[1].value}")

    def rule_as_known(self, scheme_id: str, transaction_kind: TransactionKind, *,
                      transaction_at: datetime) -> SchemeTransactionRule:
        if transaction_at.tzinfo is None or transaction_at.utcoffset() is None:
            raise FundTransactionError("transaction_at must be timezone aware")
        local = transaction_at.astimezone(IST)
        candidates = [rule for rule in self.rules
                      if rule.scheme_id == scheme_id
                      and rule.transaction_kind is TransactionKind(transaction_kind)
                      and rule.effective_from <= local.date()
                      and (rule.effective_to is None or local.date() <= rule.effective_to)
                      and rule.published_at.astimezone(IST) <= local]
        if len(candidates) != 1:
            raise FundTransactionError(
                f"no unique published transaction rule for {scheme_id} "
                f"{TransactionKind(transaction_kind).value} on {local.date()}"
            )
        return candidates[0]

    def assign_nav_date(self, scheme_id: str, transaction_kind: TransactionKind, *,
                        received_at: datetime) -> date:
        """Apply the scheme's sourced cutoff/lag and official business calendar."""
        try:
            local = received_at.astimezone(IST)
            if received_at.tzinfo is None or received_at.utcoffset() is None:
                raise FundTransactionError("received_at must be timezone aware")
            calendar = self.calendars[scheme_id]
            rule = self.rule_as_known(scheme_id, transaction_kind, transaction_at=received_at)
            is_open = calendar.is_open(local.date())
        except KeyError as exc:
            raise FundTransactionError(f"unknown scheme calendar {scheme_id}") from exc
        except TradingClockError as exc:
            raise FundTransactionError(str(exc)) from exc

        if is_open:
            lag = rule.before_cutoff_lag if local.time().replace(tzinfo=None) <= rule.cutoff_local else rule.after_cutoff_lag
            target = local.date()
            if lag == 0:
                return target
        else:
            lag = rule.closed_day_lag
            candidates = [day for day in calendar.open_days if day > local.date()]
            if not candidates:
                raise FundTransactionError(f"no sourced open day after closed date {local.date()}")
            target = min(candidates)

        for _ in range(lag):
            try:
                target = calendar.next_open(target)
            except TradingClockError as exc:
                raise FundTransactionError(str(exc)) from exc
        return target


__all__ = [
    "FundTransactionError", "SchemeTransactionCalendar", "SchemeTransactionRule",
    "TransactionKind",
]
