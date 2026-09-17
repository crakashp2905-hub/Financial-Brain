"""Parallel prefetch: fill the raw lake from the network, touching no database.

Backfilling a decade is network-bound, but the warehouse is single-writer (ADR-0001), so
interleaving fetch and load serialises the whole job behind one connection. Measured on
the 2015-2026 backfill: ~15 payloads/minute serially, which is roughly three hours.

Splitting the two stages fixes it without adding a scheduler:

    fb prefetch --start 2015-01-01 --end 2026-09-16    # parallel, network only
    fb ingest --start 2015-01-01 --end 2026-09-16 --from-lake   # serial, DB only

The second stage needs no network at all, which also means a re-ingest after a parser
change costs nothing. Prefetch writes only to distinct filesystem paths, so it is safe to
run concurrently; nothing here opens the database.

Concurrency is deliberately modest. These are public archive files, but hammering an
exchange is neither polite nor in our interest.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date

from ..config import Config, load
from ..lake.store import RawLake
from ..providers.base import FetchError, NotPublished
from ..providers.bhavcopy import PROVIDERS
from ..providers.reference import NSEIndexCloseProvider
from .job import business_days

DEFAULT_WORKERS = 6


@dataclass
class PrefetchStats:
    fetched: int = 0
    already_present: int = 0
    not_published: int = 0
    failed: int = 0
    bytes_fetched: int = 0
    failures: list = None

    def __post_init__(self):
        if self.failures is None:
            self.failures = []

    def __str__(self) -> str:
        return (f"fetched={self.fetched} cached={self.already_present} "
                f"not_published={self.not_published} failed={self.failed} "
                f"({self.bytes_fetched / 1e6:.1f} MB)")


def _provider_for(kind: str, source: str):
    if kind == "index":
        return NSEIndexCloseProvider()
    return PROVIDERS[source]()


def prefetch(start: date, end: date, *, sources: list[str] | None = None,
             kind: str = "bhavcopy", workers: int = DEFAULT_WORKERS,
             cfg: Config | None = None, progress=None) -> PrefetchStats:
    """Download every payload in the range into the lake, in parallel."""
    cfg = cfg or load()
    lake = RawLake(cfg.lake)
    stats = PrefetchStats()
    sources = sources or (["NSE"] if kind == "index" else ["NSE", "BSE"])

    tasks = [(src, d) for src in sources for d in business_days(start, end)]

    def one(src: str, d: date):
        provider = _provider_for(kind, src)
        # A day already in the lake is never re-fetched: the lake is immutable and the
        # whole point is that we do not go back to the source for bytes we hold.
        day_dir = (cfg.lake / provider.source / provider.dataset /
                   f"{d:%Y}" / f"{d:%m}" / f"{d:%d}")
        if day_dir.exists() and any(day_dir.glob("*.meta.json")):
            return ("cached", 0, None)
        try:
            res = provider.fetch(d)
            lake.put(source=provider.source, dataset=provider.dataset, business_date=d,
                     filename=res.filename, payload=res.payload, url=res.url,
                     content_type=res.content_type, http_status=res.http_status,
                     retrieved_at=res.retrieved_at)
            return ("fetched", len(res.payload), None)
        except NotPublished:
            return ("not_published", 0, None)
        except FetchError as e:
            return ("failed", 0, f"{src} {d}: {e}")
        except Exception as e:  # noqa: BLE001 - one bad day must not kill a decade
            return ("failed", 0, f"{src} {d}: {type(e).__name__}: {e}")

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, src, d): (src, d) for src, d in tasks}
        for fut in as_completed(futures):
            outcome, nbytes, err = fut.result()
            if outcome == "fetched":
                stats.fetched += 1
                stats.bytes_fetched += nbytes
            elif outcome == "cached":
                stats.already_present += 1
            elif outcome == "not_published":
                stats.not_published += 1
            else:
                stats.failed += 1
                if err:
                    stats.failures.append(err)
            done += 1
            if progress and done % 100 == 0:
                progress(done, len(tasks), stats)
    return stats
