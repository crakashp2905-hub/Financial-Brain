import pandas as pd
import pytest

from financial_brain.opportunity import learning


class Result:
    def __init__(self, frame):
        self._frame = frame

    def df(self):
        return self._frame


class Connection:
    def __init__(self, frame):
        self.frame = frame
        self.query = None

    def execute(self, query, params):
        self.query, self.params = query, params
        return Result(self.frame)


def _rows(sessions=10, disposition="REJECTED_FILTER"):
    rows = []
    for session in range(sessions):
        for name in range(20):
            rows.append({"session": session, "lineage": f"L{name}", "disposition": disposition,
                         "reason": "REJECT_COST", "fwd_excess": 0.01,
                         "adv20": float(name), "vol_60": float(name), "mom_12_1": float(name)})
    return pd.DataFrame(rows)


def test_memory_frame_queries_only_resolved_rows_for_the_requested_run():
    frame = _rows(1)
    con = Connection(frame)
    assert learning.frame(con, "run-7") is frame
    assert "m.fwd_excess IS NOT NULL" in con.query
    assert con.params == ["run-7"]
    with pytest.raises(learning.LearningError, match="no resolved memory"):
        learning.frame(Connection(pd.DataFrame()), "missing")


def test_systematic_misses_requires_minimum_sessions_and_only_tests_rejections():
    too_short = learning.systematic_misses(_rows(10), min_sessions=30)
    assert too_short["groups_examined"] == 0
    assert too_short["flagged"].empty
    accepted_only = learning.systematic_misses(_rows(40, disposition="TAKEN"), min_sessions=30)
    assert accepted_only["groups_examined"] == 0
    assert accepted_only["chance_note"].startswith("0 groups")


def test_constant_session_returns_do_not_create_infinite_t_statistics():
    result = learning.systematic_misses(_rows(35), t_flag=3.0, min_sessions=30)
    assert result["groups_examined"] > 0
    assert (result["all"]["t"] == 0.0).all()
    assert result["flagged"].empty
