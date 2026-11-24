from __future__ import annotations

import pytest

from financial_brain.paper.today_screen import render


def test_today_screen_exposes_all_desks_and_searchable_paper_observation() -> None:
    page = render([{"desk": "EQUITIES", "status": "WATCH", "symbol": "ABC",
                    "title": "Research watch", "summary": "No validated edge",
                    "evidence": "EXPLORATORY", "asof": "2026-11-25"}],
                  asof="2026-11-25", stale_count=1, risk_summary="kill switch OK")
    assert "INTRADAY: 0 (no observations supplied)" in page
    assert "OPTIONS: 0 (no observations supplied)" in page
    assert "MUTUAL_FUNDS: 0 (no observations supplied)" in page
    assert "data-status='WATCH'" in page
    assert "data-search='abc research watch no validated edge exploratory'" in page
    assert "No trade is the default" in page


def test_today_screen_escapes_untrusted_market_text() -> None:
    page = render([{"desk": "EQUITIES", "status": "WATCH", "symbol": "<script>",
                    "title": "<img src=x onerror=alert(1)>", "summary": "safe",
                    "evidence": "UNVERIFIED"}], asof="2026-11-25", stale_count=0,
                  risk_summary="risk")
    assert "&lt;script&gt;" in page
    assert "&lt;img src=x onerror=alert(1)&gt;" in page
    assert "<img src=x onerror=alert(1)>" not in page


def test_today_screen_rejects_unknown_actions_or_desks() -> None:
    with pytest.raises(ValueError, match="unknown desk or decision status"):
        render([{"desk": "EQUITIES", "status": "PLACE_ORDER"}],
               asof="2026-11-25", stale_count=0, risk_summary="none")
    with pytest.raises(ValueError, match="unknown desk or decision status"):
        render([{"desk": "CRYPTO", "status": "WATCH"}],
               asof="2026-11-25", stale_count=0, risk_summary="none")
