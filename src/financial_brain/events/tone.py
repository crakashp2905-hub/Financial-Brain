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


DAILY_LIMIT = 150               # ~6.8 s per filing on this CPU: cap what a daily run costs


def classify_day(con, d: date, *, materiality: tuple[str, ...] = ("high",),
                 limit: int | None = None) -> dict:
    steps = router.plan(con, TASK)
    if not steps:
        return {"skipped": "no benchmarked model for sentiment"}
    marks = ",".join("?" * len(materiality))
    rows = con.execute(f"""
        SELECT a.news_id, a.headline, a.subject FROM announcements a
        LEFT JOIN announcement_tone t ON t.news_id = a.news_id
        WHERE a.business_date = ? AND a.materiality IN ({marks}) AND t.news_id IS NULL
        ORDER BY a.published_at""", [d, *materiality]).fetchall()
    if limit:
        rows = rows[:limit]
    counts = {"classified": 0, "accepted": 0}
    for nid, head, subj in rows:
        text = head if head and len(head) >= 25 else (subj or head or "")
        r = router.decide(con, TASK, {"state": text}, steps=steps)
        con.execute("""INSERT INTO announcement_tone (news_id, tone, confidence, model,
                       accepted, route, classified_at) VALUES (?,?,?,?,?,?,?)""",
                    [nid, r.decision.label, r.decision.confidence, r.decision.model,
                     r.accepted, str(r.route), datetime.now(timezone.utc)])
        counts["classified"] += 1
        counts["accepted"] += r.accepted
    return counts
