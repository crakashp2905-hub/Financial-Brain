from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from financial_brain.instruments.mf_scheme import (
    AssetType,
    LoadSchedule,
    LoadStep,
    Option,
    Plan,
    SchemeError,
    SchemeKey,
    SchemeMaster,
    SchemeVersion,
    Structure,
)

DIR = SchemeKey("100", Plan.DIRECT, Option.GROWTH, "INF000D")
REG = SchemeKey("101", Plan.REGULAR, Option.GROWTH, "INF000R")


def ver(on, cat="Large Cap", ter="0.5", status="ACTIVE"):
    return SchemeVersion(on, "AMFI", cat, AssetType.EQUITY, Structure.OPEN_ENDED, False, "X AMC",
                         "NIFTY 100 TRI", ter=D(ter), status=status)


def master():
    m = SchemeMaster()
    m.add(DIR, ver(date(2020, 1, 1)))
    m.add(DIR, ver(date(2024, 6, 1), cat="Flexi Cap", ter="0.6"))
    m.add(REG, ver(date(2020, 1, 1), ter="1.5"))
    m.add(REG, ver(date(2025, 3, 1), status="MERGED"))
    return m


def test_reclassification_visible_only_from_its_date():
    m = master()
    assert m.as_of(DIR, date(2024, 5, 31)).category == "Large Cap"
    assert m.as_of(DIR, date(2024, 6, 1)).category == "Flexi Cap"
    assert m.as_of(DIR, date(2019, 1, 1)) is None


def test_direct_and_regular_are_separate_classes():
    m = master()
    assert m.as_of(DIR, date(2023, 1, 1)).ter != m.as_of(REG, date(2023, 1, 1)).ter
    assert DIR != REG


def test_merged_scheme_stays_in_history():
    m = master()
    assert REG in m.universe(date(2024, 1, 1))
    assert REG not in m.universe(date(2025, 6, 1))


def test_duplicate_version_date_refused():
    m = master()
    with pytest.raises(SchemeError):
        m.add(DIR, ver(date(2020, 1, 1)))


def test_exit_load_per_lot_uses_schedule_at_purchase():
    m = master()
    m.add_loads(DIR, LoadSchedule(date(2020, 1, 1), (LoadStep(365, D("0.01")),)))
    m.add_loads(DIR, LoadSchedule(date(2026, 1, 1), (LoadStep(90, D("0.005")), LoadStep(365, D("0.01")))))
    assert m.exit_load(DIR, date(2025, 6, 1), date(2025, 9, 1)) == D("0.01")
    assert m.exit_load(DIR, date(2026, 2, 1), date(2026, 3, 1)) == D("0.005")
    assert m.exit_load(DIR, date(2024, 1, 1), date(2026, 1, 1)) == 0
    with pytest.raises(SchemeError):
        m.exit_load(REG, date(2025, 1, 1), date(2025, 2, 1))
