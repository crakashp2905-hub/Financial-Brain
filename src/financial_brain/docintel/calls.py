"""Earnings calls: transcripts and investor presentations (C11, research depth).

Indian companies file the transcript of every quarterly call, and the slide deck that
goes with it. The archive holds ~18k of them with attachments, and until now the system
read none: it knew a call *happened*. MiMIC (arXiv 2504.09257) makes the case that both
carry signal in this market, and that presentations - usually discarded - are worth
keeping. That much this module takes; its headline result (a next-day price regression at
0.334 MAPE, without costs or a naive baseline) it does not.

What is built here is perception, not prediction:

* **Structure.** A transcript splits into prepared management commentary and the Q&A that
  follows. The split matters: management chooses every word of the first, and analysts
  choose the second, so a claim from one is not the same kind of claim as the other.
* **Retrieval.** Each document is embedded with ``nomic-embed-text`` locally, so "which
  past calls does this one resemble?" is a query rather than a memory.

Deliberately **not** built: any tone or sentiment reading of call text. Our own rule says
a calibration belongs to the text type it was measured on (ADR-0003), and the router's
sentiment routes were measured on filings and news headlines - not on transcripts. Call
text gets a verdict when it gets a labelled set, and not before.
"""
from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.request

from ..llm import backends
from . import extract

EMBED_MODEL = "nomic-embed-text"
# A transcript marks its turn with one of these; Indian filers are fairly consistent.
QA_MARKERS = re.compile(
    r"question[\s-]*and[\s-]*answer|q\s*&\s*a\s+session|moderator:\s*.{0,60}question|"
    r"we\s+will\s+now\s+begin\s+the\s+question", re.I)
PARTICIPANT = re.compile(r"^\s*([A-Z][A-Za-z.\-' ]{2,40}):\s", re.M)


def segments(text: str) -> dict:
    """Prepared commentary, then Q&A. When no marker is found the whole document is
    commentary - a presentation deck has no Q&A, and saying so beats guessing."""
    m = QA_MARKERS.search(text or "")
    if not m:
        return {"commentary": text or "", "qa": "", "has_qa": False}
    return {"commentary": text[:m.start()], "qa": text[m.start():], "has_qa": True}


def speakers(text: str, limit: int = 12) -> list[str]:
    """Who spoke, in order of first appearance - the management side is usually the first
    few names after the moderator."""
    seen: list[str] = []
    for name in PARTICIPANT.findall(text or ""):
        clean = " ".join(name.split())
        if clean and clean not in seen and len(clean.split()) <= 5:
            seen.append(clean)
        if len(seen) >= limit:
            break
    return seen


def embed(text: str, *, model: str = EMBED_MODEL, timeout: int = 120) -> list[float]:
    """A local embedding for retrieval. Raises BackendUnavailable if Ollama is not there,
    because a silent zero vector would make everything look similar to everything."""
    body = {"model": model, "prompt": text[:8000]}
    req = urllib.request.Request(f"{backends.OLLAMA}/api/embeddings",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.load(r)
    except (urllib.error.URLError, OSError) as e:
        raise backends.BackendUnavailable(f"ollama embeddings: {e}") from e
    vector = out.get("embedding") or []
    if not vector:
        raise backends.BackendUnavailable("ollama returned an empty embedding")
    return vector


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def read(payload: bytes, *, max_pages: int = 40) -> dict:
    """A call document as structure: text, its split, who spoke, and how long it is.

    ``max_pages`` is higher than for a filing because a transcript runs 20-40 pages and
    the Q&A - the part management did not script - is at the end.
    """
    text, pages = extract.text_of(payload, max_pages=max_pages)
    parts = segments(text)
    return {"pages": pages, "chars": len(text), "text": text,
            "commentary_chars": len(parts["commentary"]), "qa_chars": len(parts["qa"]),
            "has_qa": parts["has_qa"], "speakers": speakers(text),
            "scanned": extract.looks_scanned(text, pages)}


def most_similar(con, news_id: str, *, k: int = 5, same_company: bool = False
                 ) -> list[dict]:
    """The past calls this one most resembles. Retrieval only - similarity is not a
    forecast, and nothing here says the resemblance predicts anything."""
    row = con.execute("""SELECT c.isin, c.embedding FROM call_documents c
                         WHERE c.news_id = ? AND c.embedding IS NOT NULL""",
                      [news_id]).fetchone()
    if not row:
        return []
    isin, vector = row[0], json.loads(row[1])
    where = "AND c.isin = ?" if same_company else ""
    args = [news_id, isin] if same_company else [news_id]
    rows = con.execute(f"""SELECT c.news_id, c.isin, c.company, c.called_on, c.embedding
                           FROM call_documents c
                           WHERE c.news_id <> ? AND c.embedding IS NOT NULL {where}""",
                       args).fetchall()
    scored = [{"news_id": r[0], "isin": r[1], "company": r[2], "called_on": r[3],
               "similarity": round(cosine(vector, json.loads(r[4])), 4)} for r in rows]
    return sorted(scored, key=lambda x: -x["similarity"])[:k]
