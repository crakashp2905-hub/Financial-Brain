"""C02 - Data provider abstraction.

One interface, many providers. Nothing above this layer knows whether a byte came from
an exchange archive, a broker API, OpenBB, or a file on disk.

This is the boundary that keeps licensing out of the architecture: an AGPL provider
(OpenBB) or a paid one (Kite) is *an implementation of Provider*, never something the
rest of the system imports directly. See docs/RESOURCES.md.
"""
from __future__ import annotations

import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class FetchError(RuntimeError):
    """Retryable or terminal failure while fetching from a source."""

    def __init__(self, message: str, *, retryable: bool = True, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class NotPublished(FetchError):
    """The source has no data for this date - a holiday, weekend, or not-yet-published.

    Distinct from a failure: there is nothing to retry and nothing to quarantine.
    """

    def __init__(self, message: str):
        super().__init__(message, retryable=False, status=404)


@dataclass
class FetchResult:
    payload: bytes
    url: str
    retrieved_at: datetime
    filename: str
    content_type: str = "application/octet-stream"
    http_status: int = 200
    extra: dict = field(default_factory=dict)


class Provider(ABC):
    """A source of raw payloads for a given business date."""

    #: Short stable identifier, also the lake folder name, e.g. "NSE".
    source: str
    #: What kind of data this provider yields, e.g. "bhavcopy_cm".
    dataset: str
    #: Source tier per the hierarchy in docs/ARCHITECTURE.md.
    tier: int = 3

    @abstractmethod
    def fetch(self, business_date: date) -> FetchResult:
        """Return the raw payload for ``business_date``.

        Raises NotPublished when the source legitimately has nothing (holiday), and
        FetchError otherwise.
        """

    # -- shared HTTP helper --------------------------------------------------
    def _http_get(
        self,
        url: str,
        *,
        headers: dict | None = None,
        timeout: int = 45,
        retries: int = 3,
        backoff: float = 2.0,
    ) -> tuple[bytes, int, str]:
        hdrs = {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
            **(headers or {}),
        }
        last: Exception | None = None
        for attempt in range(retries):
            try:
                req = urllib.request.Request(url, headers=hdrs)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return (
                        resp.read(),
                        resp.status,
                        resp.headers.get("Content-Type", "application/octet-stream"),
                    )
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    # "No such file" - usually no session that day; the caller decides.
                    raise FetchError(f"HTTP 404 for {url}", retryable=False, status=404)
                # 403 is bot protection, not absence. It used to be treated like 404,
                # so a transient block became a phantom holiday: NSE's 2019-10-27
                # Muhurat session was silently skipped that way. Retry it, then fail
                # loudly (status 403) rather than report "not published".
                last = e
                if e.code == 403 and attempt == retries - 1:
                    raise FetchError(f"HTTP 403 (blocked) for {url} after {retries} attempts",
                                     retryable=True, status=403) from e
            except Exception as e:  # noqa: BLE001 - network is genuinely unpredictable
                last = e
            if attempt < retries - 1:
                time.sleep(backoff * (2**attempt))
        raise FetchError(f"failed after {retries} attempts: {last}", retryable=True)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)
