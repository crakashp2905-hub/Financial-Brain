from __future__ import annotations

import pytest

from financial_brain.kernel.batch20k_sandbox import (
    Inbox,
    MemoryStore,
    NotificationSettings,
    adapter_available,
    call_adapter,
    ips_value,
    keep_latest,
    lineage_report,
    parse_plan,
    replay_alerts,
    search_docs,
    shadow_agreement,
    suggest_only,
)


def test_inverse_propensity_estimate_weights_by_logged_probability() -> None:
    assert ips_value([1.0, 0.0], [0.5, 0.5], [1.0, 1.0]) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        ips_value([1.0], [0.0], [1.0])


def test_sandbox_suggestions_are_never_executed() -> None:
    assert suggest_only("BUY NIFTY") == {"action": "BUY NIFTY", "executed": False}


def test_shadow_decisions_agree_with_live_but_are_never_sent() -> None:
    out = shadow_agreement(["BUY", "HOLD"], ["BUY", "SELL"])
    assert out["agreement"] == pytest.approx(0.5)
    assert all(r["sent"] is False for r in out["records"])


def test_lineage_lists_everything_a_decision_depends_on() -> None:
    assert lineage_report({"d": ["c"], "c": ["b"]}, "d") == ["b", "c"]


def test_optional_adapter_absence_is_reported_not_raised() -> None:
    assert adapter_available("definitely_missing_package_xyz") is False
    assert adapter_available("json") is True


def test_paper_plan_parses_to_typed_fields_and_rejects_others() -> None:
    plan = parse_plan("buy 10 NIFTY at 100")
    assert plan == {"side": "BUY", "qty": 10, "symbol": "NIFTY", "price": 100.0, "mode": "PAPER"}
    with pytest.raises(ValueError):
        parse_plan("sell x NIFTY at 100")


def test_session_memory_prunes_past_retention_and_deletes_on_request() -> None:
    mem = MemoryStore(retain_days=5)
    mem.put("a", 1, day=0)
    mem.put("b", 2, day=9)
    assert mem.prune(today=10) == ["a"]
    assert mem.get("b") == 2
    mem.delete("b")
    with pytest.raises(KeyError):
        mem.get("b")


def test_offline_retrieval_needs_every_query_word() -> None:
    docs = {"n1": "momentum rules for trend", "n2": "breakout notes"}
    assert search_docs(docs, "momentum rules") == ["n1"]
    assert search_docs(docs, "rules") == ["n1"]
    assert search_docs(docs, "momentum breakout") == []


def test_inbox_tracks_unread_messages() -> None:
    box = Inbox()
    box.add("signal fired")
    box.add("fill received")
    assert len(box.unread()) == 2
    box.mark_read(0)
    assert box.unread() == ["fill received"]
    with pytest.raises(IndexError):
        box.mark_read(9)


def test_alert_replay_reports_first_crossings() -> None:
    assert replay_alerts([99.0, 101.0, 102.0, 98.0, 103.0], threshold=100.0) == [1, 4]


def test_notifications_are_off_by_default() -> None:
    settings = NotificationSettings()
    assert settings.is_on("orb") is False
    settings.set_on("orb", True)
    assert settings.is_on("orb") is True


def test_retention_keeps_only_the_newest() -> None:
    assert keep_latest([("a", 1), ("b", 2), ("c", 3)], n=2) == ["a"]
    with pytest.raises(ValueError):
        keep_latest([], n=-1)


def test_external_adapters_are_disabled_unless_enabled() -> None:
    with pytest.raises(PermissionError, match="disabled by default"):
        call_adapter("webhook", {})
    assert call_adapter("webhook", {"webhook": True}) == "CALLED"
