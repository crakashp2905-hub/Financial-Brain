"""FE-0060 dated, versioned cost schedule line items."""
from datetime import date

import pytest

from financial_brain.costs.india import CostModel, Segment, Side
from financial_brain.forward_edge.cost_schedule import CostSchedule, CostScheduleBook, CostScheduleError


def _schedule(version="v1", effective_from=date(2026, 1, 1), effective_to=date(2026, 6, 30), model=None):
    return CostSchedule("india-equity", version, effective_from, effective_to, Segment.DELIVERY,
                        model or CostModel(), "owner-configured-tariff", "a" * 64)


def test_effective_cost_resolution_returns_each_charge_with_schedule_lineage() -> None:
    book = CostScheduleBook((_schedule(), _schedule("v2", date(2026, 7, 1), None)))
    resolved = book.resolve(schedule_id="india-equity", segment=Segment.DELIVERY,
                            day=date(2026, 11, 15))
    buy = resolved.one_way(day=date(2026, 11, 15), turnover=100_000, side=Side.BUY, bucket="large")
    sell = resolved.one_way(day=date(2026, 11, 15), turnover=100_000, side=Side.SELL, bucket="large")
    assert buy["schedule_version"] == "v2" and len(buy["schedule_sha256"]) == 64
    assert buy["stt"] > 0 and buy["stamp"] > 0 and buy["gst"] > 0
    assert sell["dp"] > 0 and sell["schedule_sha256"] == buy["schedule_sha256"]
    assert buy["total"] == pytest.approx(sum(buy[k] for k in (
        "stt", "exchange", "sebi", "stamp", "brokerage", "gst", "dp", "impact")))
    without_impact = resolved.one_way(day=date(2026, 11, 15), turnover=100_000,
                                      side=Side.BUY, bucket="large", include_impact=False)
    assert without_impact["impact"] == 0
    assert without_impact["total"] == pytest.approx(buy["total"] - buy["impact"])


def test_schedule_identity_is_immutable_and_effective_ranges_cannot_overlap() -> None:
    book = CostScheduleBook((_schedule(),))
    with pytest.raises(CostScheduleError, match="immutable"):
        book.register(_schedule(model=CostModel(stt_delivery=0.002)))
    with pytest.raises(CostScheduleError, match="overlap"):
        book.register(_schedule("v2", date(2026, 6, 30), None))


def test_missing_or_out_of_effective_date_schedule_fails_closed() -> None:
    book = CostScheduleBook((_schedule(),))
    with pytest.raises(CostScheduleError, match="no unique"):
        book.resolve(schedule_id="india-equity", segment=Segment.DELIVERY, day=date(2026, 12, 1))
    resolved = book.resolve(schedule_id="india-equity", segment=Segment.DELIVERY, day=date(2026, 6, 30))
    with pytest.raises(CostScheduleError, match="not effective"):
        resolved.one_way(day=date(2026, 7, 1), turnover=100_000, side=Side.BUY, bucket="large")
