from datetime import datetime, timezone

from financial_brain.signals.publication import Candidate, Desk, publish

NOW = datetime(2026, 11, 23, 10, tzinfo=timezone.utc)


def candidate(i, desk, score, net=0.01, *, eligible=True, event_triggered=False,
              decision_at=NOW):
    return Candidate(str(i), f"{desk.value}:{i}", desk, score, net, decision_at,
                     eligible, event_triggered)


def test_swing_cap_etf_independent_and_intraday_options_event_driven():
    rows = [candidate(i, Desk.SWING, i) for i in range(8)]
    rows += [candidate("etf", Desk.ETF, 1), candidate("no-event", Desk.OPTIONS, 2),
             candidate("event", Desk.OPTIONS, 1, event_triggered=True),
             candidate("intraday", Desk.INTRADAY, 1, event_triggered=True)]
    result = publish(rows, as_of=NOW)
    assert len(result["swing"]) == 5
    assert [x.instrument_id for x in result["swing"]] == [f"SWING:{i}" for i in (7, 6, 5, 4, 3)]
    assert len(result["etf"]) == 1
    assert [x.candidate_id for x in result["options"]] == ["event"]
    assert [x.candidate_id for x in result["intraday"]] == ["intraday"]


def test_no_trade_quota_and_negative_or_future_candidates_are_not_published():
    rows = [candidate("negative", Desk.SWING, 100, net=-0.01),
            candidate("ineligible", Desk.ETF, 1, eligible=False),
            candidate("future", Desk.ETF, 2, decision_at=NOW.replace(hour=11))]
    assert publish(rows, as_of=NOW)["swing"] == []
