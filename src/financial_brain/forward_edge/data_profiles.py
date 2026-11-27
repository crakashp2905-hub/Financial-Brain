"""Frozen per-instrument market-data requirements for the forward paper runner."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from financial_brain.forward_edge.market_data import executable_quote
from financial_brain.forward_edge.market_events import EventKind, MarketEvent


class DataProfileError(ValueError):
    """A frozen instrument data profile is absent, malformed or not executable."""


class DataProfileKind(StrEnum):
    INDEX_LEVEL_ONLY = "INDEX_LEVEL_ONLY"
    EQUITY_EXECUTION = "EQUITY_EXECUTION"
    OPTION_EXECUTION = "OPTION_EXECUTION"
    MUTUAL_FUND_NAV = "MUTUAL_FUND_NAV"


@dataclass(frozen=True)
class InstrumentDataProfile:
    instrument_id: str
    kind: DataProfileKind

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "InstrumentDataProfile":
        if not isinstance(row, dict):
            raise DataProfileError("each data profile must be an object")
        instrument_id, raw_kind = row.get("instrument_id"), row.get("profile")
        if not isinstance(instrument_id, str) or not instrument_id.strip():
            raise DataProfileError("data profile instrument_id is required")
        if not isinstance(raw_kind, str):
            raise DataProfileError("data profile name must be a string")
        try:
            kind = DataProfileKind(raw_kind)
        except (TypeError, ValueError) as exc:
            raise DataProfileError(f"unsupported data profile {raw_kind!r}") from exc
        if set(row) != {"instrument_id", "profile"}:
            raise DataProfileError("data profile fields must be instrument_id and profile")
        return cls(instrument_id.strip(), kind)


def profiles_from_constraints(constraints: dict[str, Any],
                              instrument_universe: tuple[str, ...]) -> dict[str, InstrumentDataProfile] | None:
    """Parse a complete frozen profile map; return None for legacy sessions without profiles."""
    if "data_profiles" not in constraints:
        return None
    rows = constraints["data_profiles"]
    if not isinstance(rows, list) or not rows:
        raise DataProfileError("data_profiles must be a non-empty list")
    profiles = [InstrumentDataProfile.from_dict(row) for row in rows]
    result = {profile.instrument_id: profile for profile in profiles}
    if len(result) != len(profiles):
        raise DataProfileError("data_profiles contains duplicate instruments")
    expected = set(instrument_universe)
    if set(result) != expected:
        missing, extra = sorted(expected - set(result)), sorted(set(result) - expected)
        raise DataProfileError(f"data_profiles must exactly cover the frozen universe; missing={missing}, extra={extra}")
    return result


def validate_profile_event(profile: InstrumentDataProfile, event: MarketEvent, *,
                           for_execution: bool = False) -> None:
    """Check observations against their profile and require executable depth before fills."""
    if event.instrument_id != profile.instrument_id:
        raise DataProfileError("market event instrument differs from its frozen data profile")
    if event.kind in {EventKind.HALT, EventKind.AUCTION, EventKind.CORPORATE_ACTION,
                      EventKind.BROKER_UPDATE}:
        return
    if profile.kind is DataProfileKind.INDEX_LEVEL_ONLY:
        if for_execution:
            raise DataProfileError("index level-only profile cannot supply execution data")
        if event.kind not in {EventKind.TICK, EventKind.QUOTE, EventKind.BAR}:
            raise DataProfileError("index level profile requires a tick, quote or bar observation")
        if event.kind is EventKind.TICK and "price" not in event.payload:
            raise DataProfileError("index level profile is missing its last price")
        if event.kind is EventKind.QUOTE and not any(
                key in event.payload for key in ("last_price", "bid", "ask")):
            raise DataProfileError("index level profile is missing a price observation")
        return
    if profile.kind in {DataProfileKind.EQUITY_EXECUTION, DataProfileKind.OPTION_EXECUTION}:
        if event.kind not in {EventKind.TICK, EventKind.QUOTE, EventKind.BAR}:
            raise DataProfileError("traded instrument profile requires tick, quote or bar data")
        if for_execution and executable_quote(event) is None:
            required = "option bid/ask and depth" if profile.kind is DataProfileKind.OPTION_EXECUTION else \
                "equity bid/ask and depth"
            raise DataProfileError(f"paper execution is missing required {required}")
        return
    if profile.kind is DataProfileKind.MUTUAL_FUND_NAV:
        if for_execution:
            raise DataProfileError("MF NAV is a valuation mark, not quote execution data")
        if event.kind is not EventKind.NAV:
            raise DataProfileError("mutual-fund profile requires an NAV observation")


__all__ = ["DataProfileError", "DataProfileKind", "InstrumentDataProfile",
           "profiles_from_constraints", "validate_profile_event"]
