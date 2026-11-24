from datetime import date, datetime, timezone

import pytest

from financial_brain.forward_edge.horizon_freeze import (
    HorizonChoice,
    HorizonFreeze,
    HorizonFreezeError,
    HorizonFreezeJournal,
)


def _declaration(cohort="cohort-1", choices=None, first=date(2026, 11, 16)):
    return HorizonFreeze(cohort, "momentum-v1", "strategy-sha", "swing",
                         datetime(2026, 11, 15, 10, tzinfo=timezone.utc), first,
                         tuple(choices or (HorizonChoice("sessions", 5), HorizonChoice("sessions", 20),
                                           HorizonChoice("sessions", 60))))


def test_horizon_choices_are_frozen_before_first_eligible_session(tmp_path) -> None:
    journal = HorizonFreezeJournal(tmp_path / "horizons.sqlite3")
    declaration = _declaration()
    assert journal.freeze(declaration)
    assert not journal.freeze(declaration)
    assert journal.get("cohort-1")["first_eligible_session"] == "2026-11-16"
    with pytest.raises(HorizonFreezeError, match="immutable"):
        journal.freeze(_declaration(choices=(HorizonChoice("sessions", 5),)))
    journal.close()


def test_post_start_freeze_and_mechanics_mismatch_fail_closed() -> None:
    with pytest.raises(HorizonFreezeError, match="before"):
        _declaration(first=date(2026, 11, 15))
    with pytest.raises(HorizonFreezeError, match="minute-based"):
        HorizonFreeze("intraday", "orb", "sha", "intraday",
                      datetime(2026, 11, 15, tzinfo=timezone.utc), date(2026, 11, 16),
                      (HorizonChoice("sessions", 5),))


def test_horizon_units_and_ranges_are_explicit() -> None:
    with pytest.raises(HorizonFreezeError, match="5/20/60"):
        HorizonChoice("sessions", 10)
    with pytest.raises(HorizonFreezeError, match="375-minute"):
        HorizonChoice("minutes", 400)
    intra = HorizonFreeze("intraday", "orb", "sha", "intraday",
                          datetime(2026, 11, 15, tzinfo=timezone.utc), date(2026, 11, 16),
                          (HorizonChoice("minutes", 15), HorizonChoice("minutes", 60)))
    assert intra.digest()
