"""Shareholder tone of announcements, through the model router (ADR-0002).

The deterministic classifier says *what* happened (event type); this says which way it
cuts for shareholders - positive, negative or neutral - using the cheapest model that
the benchmark measured good enough, escalating when it is unsure.

Scope is deliberately narrow: only high-materiality announcements (≈100 a day), because
tone matters where materiality is high and local inference on this laptop costs
seconds per item. Every result is stored with the model that produced it, its
confidence and whether it cleared that model's calibrated bar; an unaccepted answer is
kept but marked, and the brief never shows it as fact.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from ..llm import router

TASK = "sentiment"


DAILY_LIMIT = 150               # ~5.6 s per filing on this CPU: cap what a daily run costs


def classify_day(con, d: date, *, materiality: tuple[str, ...] = ("high",),
                 limit: int | None = None, refresh: bool = False) -> dict:
    """Classify a day's material filings. ``refresh`` also re-does rows left by a
    superseded route - otherwise one day's tone can mix verdicts from several routes,
    each with its own calibration."""
    steps = router.plan(con, TASK)
    if not steps:
        return {"skipped": "no benchmarked model for sentiment"}
    marks = ",".join("?" * len(materiality))
    # Stale = classified before the current route was chosen. Testing which models are in
    # the route would keep rows a *previous* route produced with the same model under
    # different thresholds.
    chosen = con.execute("SELECT MAX(chosen_at) FROM model_routes WHERE task = ?",
                         [TASK]).fetchone()[0]
    stale = "OR t.classified_at < ?" if (refresh and chosen) else ""
    rows = con.execute(f"""
        SELECT a.news_id, a.headline, a.subject FROM announcements a
        LEFT JOIN announcement_tone t ON t.news_id = a.news_id
        WHERE a.business_date = ? AND a.materiality IN ({marks})
          AND (t.news_id IS NULL {stale})
        ORDER BY a.published_at""",
                       [d, *materiality, *([chosen] if stale else [])]).fetchall()
    if limit:
        rows = rows[:limit]
    counts = {"classified": 0, "accepted": 0, "refreshed": 0}
    for nid, head, subj in rows:
        text = head if head and len(head) >= 25 else (subj or head or "")
        r = router.decide(con, TASK, {"state": text}, steps=steps)
        counts["refreshed"] += con.execute(
            "DELETE FROM announcement_tone WHERE news_id = ? RETURNING 1", [nid]).fetchone() is not None
        con.execute("""INSERT INTO announcement_tone (news_id, tone, confidence, model,
                       accepted, route, classified_at) VALUES (?,?,?,?,?,?,?)""",
                    [nid, r.decision.label, r.decision.confidence, r.decision.model,
                     r.accepted, str(r.route), datetime.now(timezone.utc)])
        counts["classified"] += 1
        counts["accepted"] += r.accepted
    return counts
