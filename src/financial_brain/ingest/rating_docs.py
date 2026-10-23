"""Rating-action direction read from the attached letter when the headline does not say it.

About half of the credit-rating announcements are headed only "Intimation of credit rating"; the direction is in the
PDF. For those (``rating_actions.direction = 'unclear'`` with an attachment) the document is fetched to the raw lake,
its text read (``docintel.extract.text_of``) and ``events.rating_direction.direction`` applied to the first 4,000
characters - the letter's opening paragraph and rating table, where the action is stated. Stored apart, in
``rating_pdf_direction``, so a headline reading and a document reading are never confused; ``fb rating-actions``
uses the document's direction only where the headline had none.

A letter covering several instruments can mix actions; the first rule that matches in ``rating_direction`` (withdrawn,
downgrade, upgrade, watch, outlook, reaffirmed, assigned) decides, which reads a mixed letter as its most severe
action. Scanned letters with no text stay unclear.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from ..events import rating_direction as RD
from ..lake.store import RawLake
from ..providers.base import FetchError

HEAD_CHARS = 4000


def fetch(url: str, bse):
    """BSE attachments through the BSE provider; NSE archive attachments through the NSE one."""
    if "nseindia.com" in url:
        from ..providers.base import FetchResult
        from ..providers.nse_results import NSEResults
        body, status, ctype = NSEResults(min_interval=0.5)._get(url)
        return FetchResult(payload=body, url=url, retrieved_at=datetime.now(timezone.utc), filename=url.rsplit("/", 1)[-1],
                           content_type=ctype or "application/pdf", http_status=status)
    return bse.fetch_url(url)


def run(con, cfg, *, limit: int = 500, provider=None) -> dict:
    from ..docintel import extract as EX
    from ..providers.bse_filing import BSEFiling
    provider = provider or BSEFiling()
    lake = RawLake(cfg.lake)
    rows = con.execute("""
        SELECT r.news_id, a.attachment, a.business_date FROM rating_actions r JOIN announcements a USING (news_id)
        LEFT JOIN rating_pdf_direction p USING (news_id)
        WHERE r.direction = 'unclear' AND a.attachment IS NOT NULL AND a.attachment <> '' AND p.news_id IS NULL
        ORDER BY a.business_date DESC LIMIT ?""", [limit]).fetchall()
    out = {"candidates": len(rows), "read": 0, "no_text": 0, "failed": 0}
    now = datetime.now(timezone.utc)
    for news_id, url, d in rows:
        try:
            res = fetch(url, provider)
        except FetchError:
            out["failed"] += 1
            continue
        lake.put(source="FILING", dataset="rating_letter", business_date=d or date.today(), filename=res.filename,
                 payload=res.payload, url=res.url, content_type=res.content_type, http_status=res.http_status,
                 retrieved_at=res.retrieved_at)
        try:
            text, _pages = EX.text_of(res.payload)
        except Exception:                               # noqa: BLE001 - an unreadable file is recorded, not fatal
            text = ""
        head = (text or "")[:HEAD_CHARS]
        direction = RD.direction(head) if head.strip() else "unclear"
        if not head.strip():
            out["no_text"] += 1
        con.execute("INSERT OR REPLACE INTO rating_pdf_direction VALUES (?,?,?,?,?)",
                    [news_id, direction, RD.SIGN[direction], len(text or ""), now])
        out["read"] += 1
    return out
