"""Searchable, fail-closed cross-desk Today panel for the paper terminal."""
from __future__ import annotations

import html
import json
from collections.abc import Sequence

DESKS = ("EQUITIES", "INTRADAY", "OPTIONS", "ETF", "MUTUAL_FUNDS")
STATUSES = {"BUY", "SELL", "WATCH", "NO_TRADE", "APPROVAL_NEEDED", "EXIT_TRIGGERED"}


def render(items: Sequence[dict], *, asof: str, stale_count: int, risk_summary: str) -> str:
    """Render only supplied paper observations; absent desks remain explicitly unavailable."""
    if not isinstance(asof, str) or not asof.strip() or stale_count < 0:
        raise ValueError("as-of date and non-negative stale count are required")
    normalized = []
    for item in items:
        desk = str(item.get("desk", "")).upper()
        status = str(item.get("status", "")).upper()
        if desk not in DESKS or status not in STATUSES:
            raise ValueError("unknown desk or decision status")
        normalized.append({**item, "desk": desk, "status": status})

    cards = []
    counts = {desk: 0 for desk in DESKS}
    for item in normalized:
        counts[item["desk"]] += 1
        search = " ".join(str(item.get(k, "")) for k in ("symbol", "title", "summary", "evidence", "reason")
                           if item.get(k))
        cards.append(
            f"<article class='today-card' data-desk='{item['desk']}' data-status='{item['status']}' "
            f"data-search='{html.escape(search, quote=True).lower()}'>"
            f"<b>{html.escape(item['status'])} · {html.escape(str(item.get('symbol', '—')))}</b> "
            f"<span class='today-chip'>{html.escape(item['desk'])}</span>"
            f"<div>{html.escape(str(item.get('title', '')))}</div>"
            f"<div class='today-muted'>{html.escape(str(item.get('summary', '')))}</div>"
            f"<div class='today-muted'>As of {html.escape(str(item.get('asof', asof)))} · "
            f"Evidence: {html.escape(str(item.get('evidence', 'UNVERIFIED')))}</div></article>"
        )
    desks = "".join(
        f"<span class='today-chip'>{desk}: {counts[desk]} {'' if counts[desk] else '(no observations supplied)'}</span>"
        for desk in DESKS
    )
    empty = "<p id='today-empty'>No matching observations. No trade is the default.</p>"
    script = """<script>
const q=document.getElementById('today-search'), s=document.getElementById('today-status'), d=document.getElementById('today-desk');
function filterToday(){let n=0;for(const c of document.querySelectorAll('.today-card')){const ok=c.dataset.search.includes(q.value.trim().toLowerCase())&&(!s.value||c.dataset.status===s.value)&&(!d.value||c.dataset.desk===d.value);c.hidden=!ok;if(ok)n++}document.getElementById('today-empty').hidden=n>0}
q.addEventListener('input',filterToday);s.addEventListener('change',filterToday);d.addEventListener('change',filterToday);
</script>"""
    statuses = "".join(f"<option>{x}</option>" for x in sorted(STATUSES))
    desk_options = "".join(f"<option>{x}</option>" for x in DESKS)
    # JSON encoding prevents control characters in the risk note from becoming markup.
    safe_risk = html.escape(json.dumps(risk_summary, ensure_ascii=True)[1:-1])
    return (
        "<section id='today-screen' aria-label='Searchable cross-desk Today screen'>"
        "<h2>Today · paper decisions</h2>"
        f"<div class='today-muted'>As of {html.escape(asof)} · {stale_count} stale input(s) · "
        f"Outstanding risk: {safe_risk}</div><div>{desks}</div>"
        "<label>Search <input id='today-search' type='search' aria-label='Search decisions'></label>"
        f"<label>Status <select id='today-status'><option value=''>All</option>{statuses}</select></label>"
        f"<label>Desk <select id='today-desk'><option value=''>All</option>{desk_options}</select></label>"
        + ("".join(cards) if cards else "") + empty + script + "</section>"
    )
