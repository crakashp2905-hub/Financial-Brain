from datetime import date

import pytest

from financial_brain.mf_total_return import TotalReturnError, total_return_path

HASH = "d" * 64


def nav(day, code, value):
    return {"date": date.fromisoformat(day), "scheme_code": code, "nav": value}


def event(day, kind, **kwargs):
    return {"effective_on": date.fromisoformat(day), "event_type": kind,
            "source_sha256": HASH, **kwargs}


def test_idcw_payout_offsets_nav_drop_in_economic_return():
    path = total_return_path([nav("2026-01-01", "A", 100), nav("2026-01-02", "A", 98)],
                             [event("2026-01-02", "IDCW_CASH", scheme_code="A",
                                    cash_per_unit=2)])
    assert path[-1]["wealth"] == 100
    assert path[-1]["return"] == 0


def test_split_and_reinvested_distribution_preserve_wealth():
    path = total_return_path([nav("2026-01-01", "A", 100), nav("2026-01-02", "A", 49)],
                             [event("2026-01-02", "SPLIT", scheme_code="A",
                                    units_multiplier=2, event_sequence=2),
                              event("2026-01-02", "IDCW_REINVEST", scheme_code="A",
                                    cash_per_unit=2, reinvest_nav=49, event_sequence=1)])
    assert path[-1]["wealth"] == pytest.approx(102.0)


def test_merger_requires_successor_nav_and_event_provenance():
    path = total_return_path([nav("2026-01-01", "A", 100), nav("2026-01-02", "A", 50),
                              nav("2026-01-02", "B", 200)],
                             [event("2026-01-02", "MERGER", scheme_code="A",
                                    successor_scheme_code="B", successor_units_per_unit=0.5)])
    assert path[-1]["scheme_code"] == "B"
    assert path[-1]["wealth"] == 100
    with pytest.raises(TotalReturnError):
        total_return_path([nav("2026-01-01", "A", 100), nav("2026-01-02", "A", 50)],
                          [event("2026-01-02", "MERGER", scheme_code="A",
                                 successor_scheme_code="B", successor_units_per_unit=0.5)])
