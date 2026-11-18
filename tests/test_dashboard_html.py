from __future__ import annotations

from financial_brain.paper.dashboard_html import render


def state():
    return {
        "consolidated": {"nav": 100500, "available": 50000, "blocked": 10000, "realized": 300,
                         "unrealized": "STALE", "drawdown": 0.02},
        "today": [{"level": 1, "kind": "STOP_HIT", "desk": "SWING", "at": "10:01", "text": "<b>x</b>",
                   "rule_version": "v1", "evidence_tier": "LOCAL_PAPER", "sl": 97, "tp": 106}],
        "desks": {"MUTUAL_FUND": {"clock": "daily NAV (not live)", "signals": [
            {"id": "M1", "side": "SUBSCRIBE", "close_action": "REDEEM", "sl": "NAV -10%", "tp": "NAV +20%",
             "trigger": "NAV_PENDING", "costs": "load by lot", "status": "REQUESTED"}]}},
        "health": {"status": "DEGRADED", "problems": ["stale"], "pending_mf": 1, "expiring_options": 0},
        "heatmap": [{"index_id": "NIFTY", "top": "UP", "p": 0.55, "model_version": "1", "optionable": True,
                     "at": "09:30"}],
        "option_cards": [{"token": "T", "action": "BUY CE", "entry_band": [100, 102],
                          "exit": {"sl_premium": 80, "tp_premium": 140}, "max_planned_loss": 3300,
                          "expected_net": 150}],
    }


def test_render_has_all_sections_and_escapes():
    html = render(state())
    for s in ("Consolidated", "Today", "MUTUAL_FUND", "daily NAV (not live)", "Health", "Index heatmap",
              "Option cards", "STALE", "No control on this page sends an order"):
        assert s in html
    assert "<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html
    assert "<form" not in html and "<button" not in html
