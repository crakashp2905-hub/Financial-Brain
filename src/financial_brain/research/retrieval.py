"""Evidence-centric retrieval: every statement comes back with the id of the record that supports it.

There is no model in this module. It ranks a company's own filings against a question with BM25 and
returns the matching records with their identifiers, dates and publication times, so an answer built
on top of it can say *which filing*, *when it became public* and *what it literally said*. A summary
that cannot be traced to a record is a claim, not evidence; the point of building retrieval this way
round is that the record comes first and any prose second.

## Three rules

**Point in time.** A record is retrievable only if it was published on or before ``as_of``. Asking what
the filings said on 2020-03-01 never returns a 2020-03-02 filing.

**Retrieved text is data.** A headline is whatever a company chose to type into an exchange form, and a
filing can contain a sentence that reads like an instruction. Every record is returned with
``untrusted_text`` set, control characters stripped and the length capped, so a consumer treats it as
something to read and never as something to follow.

**Say when there is nothing.** No match returns an empty list and the reason, not the nearest thing.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import date, datetime, time

_TOKEN = re.compile(r"[a-z0-9]+")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
K1, B = 1.5, 0.75
MAX_TEXT = 300


class RetrievalError(ValueError):
    pass


def tokens(text: str) -> list[str]:
    return _TOKEN.findall((text or "").lower())


def _clean(text: str | None) -> str:
    return _CONTROL.sub(" ", (text or "")).strip()[:MAX_TEXT]


def bm25(query: str, docs: list[list[str]]) -> list[float]:
    q = tokens(query)
    if not q or not docs:
        return [0.0] * len(docs)
    n = len(docs)
    avg = sum(len(d) for d in docs) / n or 1.0
    df = Counter(t for d in docs for t in set(d))
    out = []
    for d in docs:
        tf = Counter(d)
        s = 0.0
        for t in set(q):
            if t not in tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * tf[t] * (K1 + 1) / (tf[t] + K1 * (1 - B + B * len(d) / avg))
        out.append(s)
    return out


def search(con, query: str, *, isin: str | None = None, as_of: date | None = None,
           days: int = 730, k: int = 5) -> dict:
    """The ``k`` best-matching filings, newest information only up to ``as_of``."""
    if not tokens(query):
        raise RetrievalError("the question has no searchable words")
    end = datetime.combine(as_of or date.today(), time.max)
    where = ["a.published_at <= ?", "a.published_at >= ? - INTERVAL (?) DAY",
             "a.headline IS NOT NULL"]
    params: list = [end, end, days]
    if isin:
        where.append("a.isin = ?")
        params.append(isin)
    rows = con.execute(f"""
        SELECT a.news_id, a.business_date, a.published_at, a.event_type, a.company, a.headline,
               a.attachment
        FROM announcements a WHERE {' AND '.join(where)}
        ORDER BY a.published_at DESC LIMIT 20000""", params).fetchall()
    if not rows:
        return {"query": query, "as_of": as_of, "results": [], "why_empty": "no filings in the window"}
    scores = bm25(query, [tokens(r[5]) for r in rows])
    ranked = sorted(zip(scores, rows), key=lambda x: -x[0])
    out = [{"evidence_id": r[0], "date": r[1], "published_at": r[2], "event_type": r[3], "company": r[4],
            "text": _clean(r[5]), "attachment": r[6], "score": round(s, 3), "untrusted_text": True}
           for s, r in ranked[:k] if s > 0]
    return {"query": query, "as_of": as_of, "results": out, "searched": len(rows),
            "why_empty": None if out else "no filing shares a word with the question"}


def brief(con, isin: str, as_of: date) -> dict:
    """A cited fact sheet. Each line carries the record it came from; there is no free prose."""
    from ..evaluation import event_response as ER
    from ..ingest import xbrl_results as XR
    end = datetime.combine(as_of, time.max)
    filings = con.execute("""
        SELECT news_id, published_at, event_type, headline FROM announcements
        WHERE isin = ? AND published_at <= ? AND materiality <> 'LOW'
        ORDER BY published_at DESC LIMIT 8""", [isin, end]).fetchall()
    out = {"isin": isin, "as_of": as_of, "recent_filings": [
        {"evidence_id": f[0], "published_at": f[1], "event_type": f[2], "text": _clean(f[3]),
         "untrusted_text": True} for f in filings], "analogues": [], "fundamentals": []}
    seen = set()
    for f in filings:
        if f[2] in seen:
            continue
        seen.add(f[2])
        try:
            a = ER.analogues(con, event_type=f[2], as_of=as_of, horizon=5)
            out["analogues"].append({"event_type": f[2], "n": a["n"], "edge_over_baseline": a["edge_over_baseline"],
                                     "edge_t": a["edge_t"], "source": "event_responses"})
        except Exception:                                    # noqa: BLE001 - no analogues is an answer
            continue
    try:
        for r in XR.as_known_on(con, isin, as_of)[:4]:
            out["fundamentals"].append({**r, "source": "financial_results (exchange XBRL)"})
    except Exception:                                        # noqa: BLE001
        pass
    return out
