from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from financial_brain.forward_edge.market_events import EventKind, MarketEvent, MarketEventError
from financial_brain.kernel.instruments import (
    Instrument,
    InstrumentError,
    InstrumentId,
    InstrumentMaster,
    RuleVersion,
)


def test_semantic_underlying_lineage_is_part_of_canonical_identity() -> None:
    plain = InstrumentId("NSE", "ETF", "INDEXETF", underlying_index="NIFTY50 TRI")
    linked = InstrumentId("NSE", "ETF", "INDEXETF", underlying_index="NIFTY50 TRI",
                          underlying_lineage=("NIFTY50", "NIFTY50 TRI"))
    assert linked.underlying_lineage == ("NIFTY50", "NIFTY50 TRI")
    assert linked.key() != plain.key()


def test_unknown_instrument_identity_fails_closed_at_master_boundary() -> None:
    master = InstrumentMaster()
    with pytest.raises(InstrumentError, match="unknown instrument.*no trade is possible"):
        master.get(InstrumentId("NSE", "EQUITY", "UNKNOWN"))


def test_active_effective_identity_overlap_is_rejected() -> None:
    master = InstrumentMaster()
    master.add(Instrument(
        InstrumentId("NSE", "EQUITY", "ABC", isin="INE000A01001",
                     effective_from=date(2026, 1, 1), effective_to=date(2026, 6, 30)),
        rules=[RuleVersion(date(2026, 1, 1), tick=Decimal("0.05"))],
    ))
    with pytest.raises(InstrumentError, match="overlapping effective identities"):
        master.add(Instrument(
            InstrumentId("NSE", "EQUITY", "ABC", isin="INE000A01019",
                         effective_from=date(2026, 6, 30)),
            rules=[RuleVersion(date(2026, 6, 30), tick=Decimal("0.05"))],
        ))


def test_future_published_event_cannot_be_available_to_strategy() -> None:
    t0 = datetime(2026, 11, 21, 10, tzinfo=timezone.utc)
    with pytest.raises(MarketEventError, match="before publication"):
        MarketEvent(
            event_id="lookahead", kind=EventKind.TICK, venue="NSE", instrument_id="NSE:ABC",
            event_time=t0, received_at=t0, available_at=t0,
            payload={"price": 100}, source="fixture", published_at=t0.replace(second=1),
        )
