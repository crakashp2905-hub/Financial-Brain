"""The point-in-time fundamentals store, filled from exchange XBRL (Tier 1).

Three tables, all append-only:

``xbrl_filings``   one row per filing the exchange lists: what it is, when it became public, where the
                   raw file is in the lake, and what happened when it was read
``xbrl_facts``     every company-level numeric fact in each filing, long form - several hundred concepts
                   a filing, not only the seven the older ``financial_results`` carries
``financial_results``  the seven headline figures, so the existing ``as_known_on`` query and anything
                   built on it work unchanged

## Why this is point in time

``filed_at`` is the exchange's **dissemination timestamp**, the moment the filing became public, not
the period end and not the date it was ingested. A restatement is a second filing with a later
timestamp and becomes a second row; nothing is ever updated. A backtest asking what was knowable on a
date selects ``filed_at <= date`` and gets exactly that, which is the property no compiled-fundamentals
vendor can give back after the fact.

## What is stored and what is refused

* a filing with no XBRL link (before the XBRL era, around the quarter ended December 2018) is recorded
  as ``no_xbrl`` rather than skipped silently, so coverage is a query and not a guess;
* a filing whose XBRL period disagrees with the exchange's own listing is recorded as
  ``period_mismatch`` and its figures are not promoted to ``financial_results``;
* a *cumulative* filing (a half-year or nine-month statement) keeps its facts but is not promoted,
  because its current-period figures are not a quarter;
* ``checks_passed`` records whether the statement's own arithmetic held. It is kept, not enforced, so
  a failure is visible and countable instead of vanishing.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from ..docintel import xbrl_results as X
from ..lake.store import RawLake
from ..providers.base import FetchError
from ..providers.nse_results import NSEResults
from .corpact_feed import _register

SINCE = date(2018, 10, 1)
STORED, NO_XBRL, FETCH_FAILED, PARSE_FAILED, MISMATCH = (
    "stored", "no_xbrl", "fetch_failed", "parse_failed", "period_mismatch")
FINAL = (STORED, NO_XBRL, MISMATCH)           #: statuses a rerun does not retry


def _ts(s: str | None) -> datetime | None:
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M"):
        try:
            return datetime.strptime((s or "").strip(), fmt)
        except ValueError:
            continue
    return None


def _d(s: str | None) -> date | None:
    try:
        return datetime.strptime((s or "").strip(), "%d-%b-%Y").date()
    except ValueError:
        return None


def _basis(row: dict) -> str:
    return "consolidated" if (row.get("consolidated") or "").strip().lower() == "consolidated" \
        else "standalone"


def _record(con, row: dict, status: str, lake_key: str | None = None) -> None:
    con.execute("""
        INSERT INTO xbrl_filings (seq_number, symbol, isin, company, period_start, period_end, basis,
                                  relating_to, audited, cumulative, is_bank, filed_at, xbrl_url,
                                  lake_key, status, attempted_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT (seq_number) DO UPDATE SET status = excluded.status, lake_key = excluded.lake_key,
                                               attempted_at = excluded.attempted_at""",
                [str(row["seqNumber"]), row.get("symbol"), row.get("isin"), row.get("companyName"),
                 _d(row.get("fromDate")), _d(row.get("toDate")), _basis(row), row.get("relatingTo"),
                 row.get("audited"), (row.get("cumulative") or "").lower().startswith("cumul"),
                 (row.get("bank") or "N") == "Y",
                 _ts(row.get("exchdisstime")) or _ts(row.get("broadCastDate")), row.get("xbrl"),
                 lake_key, status, datetime.now(timezone.utc)])


def _store_facts(con, seq: str, inst: X.Instance) -> int:
    rows = [(seq, name, ctx, v) for ctx, facts in inst.facts.items() for name, v in facts.items()]
    if rows:
        con.executemany("""INSERT INTO xbrl_facts (seq_number, concept, ctx, value) VALUES (?,?,?,?)
                           ON CONFLICT DO NOTHING""", rows)
    return len(rows)


def _promote(con, row: dict, inst: X.Instance, lake_key: str) -> bool:
    """Write the headline figures to ``financial_results`` for a non-cumulative quarterly filing."""
    canon = inst.canonical("Q")
    if not row.get("isin") or not ({"revenue", "total_income"} & canon.keys()):
        return False
    filed = _ts(row.get("exchdisstime")) or _ts(row.get("broadCastDate"))
    period_end = _d(row.get("toDate"))
    if filed is None or period_end is None:
        return False
    ck = X.checks(canon)
    con.execute("""INSERT INTO financial_results (isin, company, period_end, basis, filed_at, news_id,
                   revenue, other_income, total_income, total_expenses, pbt, pat, eps_basic,
                   unit_multiplier, checks_passed, lake_key, source_page, observed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT (isin, period_end, basis, filed_at) DO NOTHING""",
                [row["isin"], row.get("companyName"), period_end, _basis(row), filed,
                 "NSE:" + str(row["seqNumber"]), canon.get("revenue"), canon.get("other_income"),
                 canon.get("total_income"), canon.get("total_expenses"), canon.get("pbt"),
                 canon.get("pat"), canon.get("eps_basic"), 1, bool(ck) and all(ck.values()),
                 lake_key, 0, datetime.now(timezone.utc)])
    return True


def ingest_symbol(con, lake: RawLake, provider: NSEResults, symbol: str, *,
                  since: date = SINCE, limit: int | None = None) -> dict:
    stats = {"symbol": symbol, "listed": 0, STORED: 0, NO_XBRL: 0, FETCH_FAILED: 0,
             PARSE_FAILED: 0, MISMATCH: 0, "skipped": 0, "facts": 0}
    rows = [r for r in provider.filings(symbol) if (_d(r.get("toDate")) or date.min) >= since]
    stats["listed"] = len(rows)
    done = {r[0] for r in con.execute(
        "SELECT seq_number FROM xbrl_filings WHERE status IN (?,?,?) AND symbol = ?",
        [*FINAL, symbol]).fetchall()}
    for row in rows[:limit] if limit else rows:
        seq = str(row["seqNumber"])
        if seq in done:
            stats["skipped"] += 1
            continue
        if not provider.has_xbrl(row):
            _record(con, row, NO_XBRL)
            stats[NO_XBRL] += 1
            continue
        try:
            res = provider.fetch_xbrl(row["xbrl"])
        except FetchError:
            _record(con, row, FETCH_FAILED)
            stats[FETCH_FAILED] += 1
            continue
        filed = _ts(row.get("exchdisstime")) or _ts(row.get("broadCastDate"))
        obj = lake.put(source="NSE", dataset="xbrl_results", business_date=(filed or datetime.now()).date(),
                       filename=res.filename, payload=res.payload, url=res.url,
                       content_type=res.content_type, http_status=res.http_status,
                       retrieved_at=res.retrieved_at)
        _register(con, obj)
        try:
            inst = X.parse(res.payload)
        except X.XBRLError:
            _record(con, row, PARSE_FAILED, obj.key)
            stats[PARSE_FAILED] += 1
            continue
        want_end = _d(row.get("toDate"))
        got_end = date.fromisoformat(inst.period[1]) if inst.period and inst.period[1] else None
        if want_end and got_end and want_end != got_end:
            _record(con, row, MISMATCH, obj.key)
            stats[MISMATCH] += 1
            continue
        stats["facts"] += _store_facts(con, seq, inst)
        if not (row.get("cumulative") or "").lower().startswith("cumul"):
            _promote(con, row, inst, obj.key)
        _record(con, row, STORED, obj.key)
        stats[STORED] += 1
    return stats


def run(con, cfg, symbols: list[str], *, since: date = SINCE, provider=None, on_progress=None,
        limit_per_symbol: int | None = None) -> dict:
    provider = provider or NSEResults()
    lake = RawLake(cfg.lake)
    total = {STORED: 0, NO_XBRL: 0, FETCH_FAILED: 0, PARSE_FAILED: 0, MISMATCH: 0, "skipped": 0,
             "symbols": 0, "empty": [], "failed": []}
    for i, sym in enumerate(symbols, 1):
        try:
            s = ingest_symbol(con, lake, provider, sym, since=since, limit=limit_per_symbol)
        except FetchError as e:
            total["failed"].append((sym, str(e)[:80]))
            continue
        total["symbols"] += 1
        if s["listed"] == 0:
            total["empty"].append(sym)
        for k in (STORED, NO_XBRL, FETCH_FAILED, PARSE_FAILED, MISMATCH, "skipped"):
            total[k] += s[k]
        if on_progress:
            on_progress(i, len(symbols), sym, s, total)
    return total


def as_known_on(con, isin: str, on: date, *, basis: str = "consolidated") -> list[dict]:
    """Every period's headline figures as they were public on ``on`` (latest filing per period)."""
    rows = con.execute("""
        SELECT period_end, revenue, pat, eps_basic, filed_at FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY period_end ORDER BY filed_at DESC) rk
            FROM financial_results WHERE isin = ? AND basis = ? AND filed_at <= ?) WHERE rk = 1
        ORDER BY period_end DESC""", [isin, basis, datetime.combine(on, datetime.max.time())]).fetchall()
    return [{"period_end": r[0], "revenue": r[1], "pat": r[2], "eps_basic": r[3], "filed_at": r[4]}
            for r in rows]
