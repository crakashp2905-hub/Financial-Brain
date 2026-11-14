from __future__ import annotations

import hashlib
import json

import pytest

from financial_brain.forward_edge import cockpit
from financial_brain.forward_edge.durable import _link


STAGES = ("source_quote", "signal", "risk", "intent", "fill", "ledger", "exit", "attribution")


def _events():
    events = []
    previous = "GENESIS"
    for sequence, stage in enumerate(STAGES, 1):
        payload = {"t": stage.upper(), "trade_id": "trade-1", "value": sequence}
        digest = _link(previous, payload)
        envelope = {"seq": sequence, "prev": previous, "hash": digest, "rec": payload}
        events.append({"stage": stage, "journal_id": f"paper-journal:{sequence}",
                       "record_sha256": digest, "record": envelope,
                       "fields": {"trade_id": "trade-1", "value": sequence}})
        previous = digest
    return events


def test_trade_drilldown_links_each_field_to_verified_journal_record():
    html = cockpit.trade_drilldown(trade_id="trade-1", events=_events())
    assert "source_quote" in html and "attribution" in html
    assert "hash-verified immutable journal record" in html
    for sequence in range(1, 9):
        assert f"paper-journal:{sequence}" in html
        assert "id='record-" in html
    assert html.count("href='#record-") >= 16


@pytest.mark.parametrize("mutate, message", [
    (lambda rows: rows.pop(), "missing journal-linked stages"),
    (lambda rows: rows[1].update(fields={"value": 99}), "displayed fields do not match"),
    (lambda rows: rows[2]["record"].update(rec={"t": "TAMPERED"}), "hash does not match"),
    (lambda rows: rows.append(rows[0]), "duplicate evidence stage"),
])
def test_trade_drilldown_rejects_missing_duplicate_or_mutated_journal_evidence(mutate, message):
    events = _events()
    mutate(events)
    with pytest.raises(cockpit.CockpitError, match=message):
        cockpit.trade_drilldown(trade_id="trade-1", events=events)


def test_trade_drilldown_supports_content_addressed_non_durable_records():
    events = []
    for stage in STAGES:
        payload = {"stage": stage, "trade_id": "trade-2"}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                 allow_nan=False).encode()).hexdigest()
        events.append({"stage": stage, "journal_id": f"archive:{stage}",
                       "record_sha256": digest, "record": payload})
    assert "archive:fill" in cockpit.trade_drilldown(trade_id="trade-2", events=events)
