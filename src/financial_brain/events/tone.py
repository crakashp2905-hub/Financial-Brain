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
from ..security import untrusted

TASK = "sentiment"
NEWS_TASK = "sentiment_news"    # headlines are a different distribution; own calibration


DAILY_LIMIT = 150               # ~5.6 s per filing on this CPU: cap what a daily run costs
DAILY_DEADLINE_S = 20 * 60      #: the evening step stops here; what is left is classified the next night
OURS = {"nomic-embed-text:latest"}   #: loaded by this project outside the tone route (call embeddings)


def ollama_busy(route_models: set[str], *, ps=None) -> str | None:
    """Why the local model server should not be used now, or None.

    Ollama serves one request per model at a time; when another program has a model loaded and working, every request
    here queues behind it for up to the client timeout (ten minutes). On 2026-10-13 and 10-14 that stalled the whole
    evening refresh. A model loaded that is neither in the tone route nor otherwise ours means another client is using
    the server - skip tonight rather than queue."""
    import json
    import urllib.request
    try:
        if ps is None:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=3) as r:
                ps = json.load(r)
    except Exception as e:                              # noqa: BLE001
        return f"model server unreachable: {e}"
    loaded = {m.get("name") for m in ps.get("models", [])}
    foreign = sorted(loaded - set(route_models) - OURS)
    return f"model server busy with another client's model(s): {', '.join(foreign)}" if foreign else None


def classify_day(con, d: date, *, materiality: tuple[str, ...] = ("high",),
                 limit: int | None = None, refresh: bool = False, deadline_s: float | None = None,
                 check_busy: bool = False) -> dict:
    """Classify a day's material filings. ``refresh`` also re-does rows left by a
    superseded route - otherwise one day's tone can mix verdicts from several routes,
    each with its own calibration."""
    # The filing route must also be current: a route calibrated under an older prompt
    # describes a system we are no longer running (ADR-0003).
    current, why = router.route_is_current(con, TASK)
    steps = router.plan(con, TASK) if current else []
    if not steps:
        return {"skipped": f"no usable route for {TASK}: {why if not current else 'none'}"}
    if check_busy:
        models = {s_["model"] for s_ in steps} | {s_["model"] for s_ in router.plan(con, NEWS_TASK)}
        busy = ollama_busy(models)
        if busy:
            return {"skipped": busy}
    import time
    t0 = time.monotonic()
    # A news headline states direction outright where a filing buries it, so thresholds
    # fitted on filings do not transfer: replayed on labelled headlines the filing route
    # was wrong 19.8% of the time it accepted, against 9.8% on filings. Headlines are
    # therefore classified only through a route verified for *them*; without one we read
    # the filing text, which is what the calibration covers.
    news_steps = router.plan(con, NEWS_TASK) if router.has_route(con, NEWS_TASK) else []
    marks = ",".join("?" * len(materiality))
    # Stale = classified before the current route was chosen. Testing which models are in
    # the route would keep rows a *previous* route produced with the same model under
    # different thresholds.
    chosen = con.execute("SELECT MAX(chosen_at) FROM model_routes WHERE task = ?",
                         [TASK]).fetchone()[0]
    # Stale two ways: an older route's calibration, or a filing whose news headline we
    # have since recovered (the text classified is no longer the best text available).
    # COALESCE: rows written before text_source existed were classified on the filing.
    changed = "OR (COALESCE(t.text_source, 'filing') = 'filing' AND n.headline IS NOT NULL)"
    stale = (f"OR t.classified_at < ? {changed}" if (refresh and chosen)
             else (changed if refresh else ""))
    rows = con.execute(f"""
        SELECT a.news_id, a.headline, a.subject, n.headline FROM announcements a
        LEFT JOIN announcement_tone t ON t.news_id = a.news_id
        LEFT JOIN announcement_news n ON n.news_id = a.news_id
        WHERE a.business_date = ?
          AND (a.materiality IN ({marks}) OR n.headline IS NOT NULL)
          AND (t.news_id IS NULL {stale})
        -- Newest first. The cap below is a budget, not a filter, and the filings a
        -- reader will see tomorrow are the ones published latest in the window that the
        -- brief covers (D 09:00 -> next session 09:00). Oldest-first spent the budget on
        -- filings that belong to the *previous* brief.
        ORDER BY a.published_at DESC""",
                       [d, *materiality, *([chosen] if (refresh and chosen) else [])]).fetchall()
    if limit:
        rows = rows[:limit]
    counts = {"classified": 0, "accepted": 0, "refreshed": 0, "on_news_headline": 0,
              "rejected_as_injection": 0}
    for nid, head, subj, news_head in rows:
        if deadline_s is not None and time.monotonic() - t0 > deadline_s:
            counts["stopped_at_deadline"] = True        # the rest are classified tomorrow (unclassified rows remain)
            break
        # An exchange clarification reads as procedural boilerplate; the news it is about
        # is what moved the price. When the filing itself carries that headline, classify
        # that instead - same filing, better text.
        filing_text = head if head and len(head) >= 25 else (subj or head or "")
        if news_head and news_steps:
            # A fetched headline is text a stranger wrote; if it tries to steer the
            # model, fall back to the filing (Tier 1, the exchange's own bytes).
            safe, found = untrusted.guard(con, news_head, subject=str(nid),
                                          where="tone_news_headline",
                                          fallback=filing_text)
            if found:
                counts["rejected_as_injection"] += 1
            if safe is news_head:
                text, src, task, use = news_head, "news_headline", NEWS_TASK, news_steps
                counts["on_news_headline"] += 1
            else:
                text, src, task, use = safe, "filing", TASK, steps
        else:
            text, src, task, use = filing_text, "filing", TASK, steps
        r = router.decide(con, task, {"state": text}, steps=use)
        counts["refreshed"] += con.execute(
            "DELETE FROM announcement_tone WHERE news_id = ? RETURNING 1", [nid]).fetchone() is not None
        con.execute("""INSERT INTO announcement_tone (news_id, tone, confidence, model,
                       accepted, route, text_source, classified_at) VALUES (?,?,?,?,?,?,?,?)""",
                    [nid, r.decision.label, r.decision.confidence, r.decision.model,
                     r.accepted, str(r.route), src, datetime.now(timezone.utc)])
        counts["classified"] += 1
        counts["accepted"] += r.accepted
    return counts
