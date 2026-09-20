"""Store as-reported financials, point in time (C03/C11).

The rule that makes this dataset worth having: **nothing is ever updated**. A company
that restates a quarter files again, and that becomes a second row with a later
``filed_at``. Asking "what did we know on 12 August 2026?" is then a query, not an
archaeology problem, and a backtest cannot quietly use figures published after the
decision it is testing.

Only statements that passed their own arithmetic (``docintel/results``) are stored. A
page whose columns were misread fails that check, and a wrong revenue figure is worse
than a missing one.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from ..docintel import results as parse
from ..evidence import ledger
from ..lake.store import RawLake
from ..providers.base import FetchError
from ..providers.bse_filing import BSEFiling
from .corpact_feed import _register

FIELDS = ("revenue", "other_income", "total_income", "total_expenses", "pbt", "pat",
          "eps_basic")


def _store(con, *, isin, company, news_id, st: parse.Statement, lake_key, filed_at,
           url) -> str | None:
    if not st.period_end:
        return None
    values = {f: (st.values[f] * st.unit if f in st.values and f != "eps_basic"
                  else st.values.get(f)) for f in FIELDS}
    con.execute("""INSERT INTO financial_results (isin, company, period_end, basis,
                   filed_at, news_id, revenue, other_income, total_income,
                   total_expenses, pbt, pat, eps_basic, unit_multiplier, checks_passed,
                   lake_key, source_page, observed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT (isin, period_end, basis, filed_at) DO NOTHING""",
                [isin, company, st.period_end, st.basis, filed_at, news_id,
                 values["revenue"], values["other_income"], values["total_income"],
                 values["total_expenses"], values["pbt"], values["pat"],
                 values["eps_basic"], st.unit, True, lake_key, st.page,
                 datetime.now(timezone.utc)])
    crore = (values["revenue"] or 0) / 10 ** 7
    return ledger.mint(
        con, kind="financial_result", subject=isin or company, as_of=st.period_end,
        claim=(f"{company}: {st.basis} revenue ₹{crore:,.0f} crore for the period "
               f"ended {st.period_end}, as reported"),
        value={"news_id": news_id, "basis": st.basis,
               "period_end": st.period_end.isoformat(),
               **{f: values[f] for f in FIELDS}, "checks": st.checks},
        source="FILING", source_tier=1, lake_key=lake_key,
        derivation="docintel/results (statement verified against its own arithmetic)",
        published_at=filed_at)


def read_day(con, cfg, d: date, *, limit: int = 10, provider=None) -> dict:
    """Parse the results filings published on a day."""
    provider = provider or BSEFiling()
    lake = RawLake(cfg.lake)
    rows = con.execute("""
        SELECT a.news_id, a.attachment, a.company, a.isin, a.published_at
        FROM announcements a
        LEFT JOIN financial_results r ON r.news_id = a.news_id
        WHERE a.business_date = ? AND a.event_type = 'RESULTS'
          AND a.attachment IS NOT NULL AND a.attachment <> '' AND a.isin IS NOT NULL
          AND r.news_id IS NULL
        ORDER BY a.published_at DESC""", [d]).fetchall()[:limit]
    stats = {"candidates": len(rows), "parsed": 0, "statements": 0, "failed": 0,
             "no_statement": 0}
    for news_id, url, company, isin, published in rows:
        try:
            res = provider.fetch_url(url)
        except FetchError:
            stats["failed"] += 1
            continue
        obj = lake.put(source="FILING", dataset="attachment", business_date=d,
                       filename=res.filename, payload=res.payload, url=res.url,
                       content_type=res.content_type, http_status=res.http_status,
                       retrieved_at=res.retrieved_at)
        _register(con, obj)
        try:
            statements = parse.read(res.payload)
        except Exception:                 # noqa: BLE001 - a broken PDF is not a crash
            stats["failed"] += 1
            continue
        stats["parsed"] += 1
        if not statements:
            stats["no_statement"] += 1
            continue
        seen = set()
        for st in statements:
            if st.basis in seen:          # the first page of each basis is the statement
                continue
            seen.add(st.basis)
            if _store(con, isin=isin, company=company, news_id=news_id, st=st,
                      lake_key=obj.key, filed_at=published, url=url):
                stats["statements"] += 1
    return stats


def as_known_on(con, isin: str, on: date, *, basis: str = "consolidated") -> list[dict]:
    """Every period's figures **as they were known on** ``on`` - the latest filing for
    each period that had been published by then. This is the query a backtest must use."""
    rows = con.execute("""
        SELECT period_end, revenue, pat, eps_basic, filed_at FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY period_end ORDER BY filed_at DESC) rk
            FROM financial_results
            WHERE isin = ? AND basis = ? AND filed_at <= ?) WHERE rk = 1
        ORDER BY period_end DESC""", [isin, basis, on]).fetchall()
    return [{"period_end": r[0], "revenue": r[1], "pat": r[2], "eps_basic": r[3],
             "filed_at": r[4]} for r in rows]
