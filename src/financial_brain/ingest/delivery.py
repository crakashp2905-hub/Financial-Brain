"""NSE delivery position (``providers/nse_delivery``) into ``delivery``: lake first, then rows.

Two phases, as every backfill here: ``prefetch`` fills the raw lake in parallel and touches no database; ``load``
reads the lake serially into ``delivery`` and needs no network. A day already in the lake is never fetched again.
Rows are filed under the trade date the file's header states.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from ..lake.store import RawLake
from ..providers.base import FetchError, NotPublished
from ..providers.nse_delivery import NseDelivery, parse

SOURCE, DATASET = "NSE", "delivery_mto"


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _filename(d: date) -> str:
    return f"MTO_{d:%d%m%Y}.DAT"


def prefetch(cfg, start: date, end: date, *, workers: int = 4, provider=None) -> dict:
    """Fill the lake for every calendar day in the range (weekend special sessions included); no database."""
    lake, provider = RawLake(cfg.lake), provider or NseDelivery()
    todo = [d for d in _days(start, end) if not lake.exists(SOURCE, DATASET, d, _filename(d))]
    out = {"asked": len(todo), "stored": 0, "not_published": 0, "failed": []}

    def one(d):
        try:
            res = provider.fetch(d)
        except NotPublished:
            return "np", d
        except FetchError as e:
            return "fail", (d, str(e)[:120])
        lake.put(source=SOURCE, dataset=DATASET, business_date=d, filename=res.filename, payload=res.payload,
                 url=res.url, content_type=res.content_type, http_status=res.http_status,
                 retrieved_at=res.retrieved_at)
        return "ok", d

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for kind, v in ex.map(one, todo):
            if kind == "ok":
                out["stored"] += 1
            elif kind == "np":
                out["not_published"] += 1
            else:
                out["failed"].append(v)
    return out


def load(con, cfg, start: date, end: date) -> dict:
    """Lake -> ``delivery`` (and ``lake_manifest``). Idempotent: a day already loaded is replaced by the same rows."""
    lake = RawLake(cfg.lake)
    out = {"days": 0, "rows": 0, "date_mismatch": []}
    now = datetime.now(timezone.utc)
    for d in _days(start, end):
        if not lake.exists(SOURCE, DATASET, d, _filename(d)):
            continue
        y, m, dd = d.isoformat().split("-")
        key = f"{SOURCE}/{DATASET}/{y}/{m}/{dd}/{_filename(d)}"
        obj = lake.meta(key)
        trade, rows = parse(lake.read(key))
        if trade != d:
            out["date_mismatch"].append((str(d), str(trade)))   # filed under the header's date regardless
        when = trade or d
        con.execute("""INSERT INTO lake_manifest (key, source, dataset, business_date, filename, url, retrieved_at,
                       sha256, size_bytes, http_status, content_type) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT (key) DO NOTHING""",
                    [obj.key, obj.source, obj.dataset, obj.business_date, obj.filename, obj.url, obj.retrieved_at,
                     obj.sha256, obj.size_bytes, obj.http_status, obj.content_type])
        con.execute("DELETE FROM delivery WHERE business_date = ?", [when])
        con.executemany("""INSERT INTO delivery (symbol, series, business_date, qty_traded, deliv_qty, deliv_pct,
                           lake_key, observed_at) VALUES (?,?,?,?,?,?,?,?)
                           ON CONFLICT (symbol, series, business_date) DO NOTHING""",
                        [[r["symbol"], r["series"], when, r["qty_traded"], r["deliv_qty"], r["deliv_pct"], key, now]
                         for r in rows])
        out["days"] += 1
        out["rows"] += len(rows)
    return out


def daily(con, cfg, d: date) -> dict:
    """The evening step: fetch today's file into the lake (if published) and load it."""
    pf = prefetch(cfg, d, d, workers=1)
    return {"prefetch": pf, "load": load(con, cfg, d, d)}
