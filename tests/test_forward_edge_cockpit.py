from datetime import datetime, timezone

import pytest

from financial_brain.forward_edge.cockpit import (
    CockpitError,
    CockpitGate,
    CockpitSnapshot,
    render_cockpit,
)


def _snapshot(**overrides):
    values = {
        "session_id": "NSE-2026-11-16",
        "as_of": datetime(2026, 11, 16, 4, tzinfo=timezone.utc),
        "market_status": "HEALTHY",
        "feed_status": "HEALTHY",
        "feed_age_seconds": 0.2,
        "max_feed_age_seconds": 1.0,
        "broker_status": "NOT_APPLICABLE",
        "risk_gates": (CockpitGate("daily-loss", "PASS", "within frozen limit"),),
        "active_paper_modes": ("shadow", "paper"),
        "source_ids": ("quote-snapshot:abc123",),
    }
    values.update(overrides)
    return CockpitSnapshot(**values)


def test_cockpit_renders_ready_only_when_required_states_are_explicit(tmp_path):
    snapshot = _snapshot()
    page = render_cockpit(snapshot, tmp_path / "cockpit.html").read_text(encoding="utf-8")
    assert snapshot.ready_for_paper
    assert "READY FOR PAPER" in page
    assert "quote-snapshot:abc123" in page
    assert snapshot.digest() in page


@pytest.mark.parametrize(("override", "reason"), [
    ({"feed_age_seconds": 2.0}, "FEED_STALE"),
    ({"feed_age_seconds": None}, "FEED_HEALTHY"),
    ({"broker_status": "UNKNOWN"}, "BROKER_UNKNOWN"),
    ({"risk_gates": (CockpitGate("margin", "UNKNOWN", "margin unresolved"),)},
     "RISK_UNKNOWN:margin:margin unresolved"),
    ({"active_paper_modes": ()}, "NO_ACTIVE_PAPER_MODE"),
    ({"risk_gates": ()}, "RISK_GATES_MISSING"),
])
def test_unknown_stale_or_blocked_snapshot_fails_closed(tmp_path, override, reason):
    snapshot = _snapshot(**override)
    assert not snapshot.ready_for_paper
    assert reason in snapshot.not_ready_reasons
    page = render_cockpit(snapshot, tmp_path / "cockpit.html").read_text(encoding="utf-8")
    assert "NOT READY" in page


def test_cockpit_html_escapes_untrusted_ids_and_validates_states(tmp_path):
    snapshot = _snapshot(session_id="<script>alert(1)</script>")
    page = render_cockpit(snapshot, tmp_path / "cockpit.html").read_text(encoding="utf-8")
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    with pytest.raises(CockpitError, match="health states"):
        _snapshot(feed_status="CONNECTED")
