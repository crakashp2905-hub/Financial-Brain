from datetime import datetime, timedelta, timezone

import duckdb
import pytest

from financial_brain.signals.sleeves import (HumanApproval, Sleeve, persist_assignments,
                                             route_to_sleeves)

NOW = datetime(2026, 11, 23, 10, tzinfo=timezone.utc)
HASH = "a" * 64


def test_human_choice_creates_separate_manual_sleeve_without_changing_auto_record():
    approval = HumanApproval("decision-1", "human:owner", NOW - timedelta(seconds=1))
    both = route_to_sleeves(signal_id="s1", signal_sha256=HASH, assigned_at=NOW,
                            autonomous_eligible=True, approval=approval)
    auto_only = route_to_sleeves(signal_id="s1", signal_sha256=HASH, assigned_at=NOW,
                                 autonomous_eligible=True)
    assert [item.sleeve for item in both] == [Sleeve.AUTONOMOUS, Sleeve.MANUAL_APPROVED]
    assert both[0] == auto_only[0]
    assert both[0].book_id != both[1].book_id


def test_human_approval_cannot_create_auto_eligibility_and_agent_cannot_approve():
    approval = HumanApproval("decision-2", "human:owner", NOW)
    routed = route_to_sleeves(signal_id="s2", signal_sha256=HASH, assigned_at=NOW,
                              autonomous_eligible=False, approval=approval)
    assert len(routed) == 1 and routed[0].sleeve == Sleeve.MANUAL_APPROVED
    with pytest.raises(ValueError, match="human approval"):
        HumanApproval("decision-3", "agent:financial-brain", NOW)


def test_assignments_persist_idempotently_in_distinct_paper_sleeve_books():
    con = duckdb.connect(":memory:")
    approval = HumanApproval("decision-4", "human:owner", NOW)
    assignments = route_to_sleeves(signal_id="s4", signal_sha256=HASH, assigned_at=NOW,
                                   autonomous_eligible=True, approval=approval)
    assert persist_assignments(con, assignments) == 2
    assert persist_assignments(con, assignments) == 2
    rows = con.execute("SELECT sleeve, book_id FROM paper_sleeve_assignments ORDER BY sleeve").fetchall()
    assert rows == [("AUTONOMOUS", "paper:auto"),
                    ("MANUAL_APPROVED", "paper:manual:decision-4")]
    con.close()
