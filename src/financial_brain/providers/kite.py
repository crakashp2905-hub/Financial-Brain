"""Zerodha Kite Connect - historical daily candles for indices (Tier 2).

Kite is the one broker API in the plan (docs/RESOURCES.md, Tier 2). Its first job is
narrow: fill index-close days NSE's own archive does not hold. Nine 2015-16 sessions
return a genuine 404 from ``nsearchives`` (see docs/PHASE-0.md), and the regime brain
needs NIFTY 50 / BANK NIFTY / sectoral closes on every session.

Rules this provider keeps:

* **Credentials only from the environment** - ``KITE_API_KEY`` and ``KITE_ACCESS_TOKEN``
  (the access token comes from Kite's daily login flow). Nothing here prompts for,
  stores or logs them. Missing credentials are a configuration error, raised before any
  network call.
* **Fill, never overwrite.** NSE is Tier 1 for index levels; Kite rows are written with
  ``source='KITE'`` and ``ON CONFLICT DO NOTHING``, so they can only occupy dates NSE
  left empty.
* **Personal use.** Kite's terms do not permit redistributing its data.

API: ``GET /instruments/historical/{token}/day?from=&to=`` with
``Authorization: token {api_key}:{access_token}`` and ``X-Kite-Version: 3``; instrument
tokens come from the ``/instruments`` dump (segment ``INDICES``), resolved by name at run
time rather than hard-coded.
"""
from __future__ import annotations

import csv
import io
import json
import os
from datetime import date

from .base import FetchError, FetchResult, Provider

API = "https://api.kite.trade"


class KiteNotConfigured(RuntimeError):
    """Raised when Kite credentials are absent. Set them in the environment."""


def credentials() -> tuple[str, str]:
    key, token = os.environ.get("KITE_API_KEY"), os.environ.get("KITE_ACCESS_TOKEN")
    if not key or not token:
        raise KiteNotConfigured(
            "Kite Connect is not configured: set KITE_API_KEY and KITE_ACCESS_TOKEN in "
            "the environment (the access token comes from Kite's daily login).")
    return key, token


class KiteIndexHistoryProvider(Provider):
    source = "KITE"
    dataset = "index_daily"
    tier = 2

    def _headers(self) -> dict:
        key, token = credentials()
        return {"X-Kite-Version": "3", "Authorization": f"token {key}:{token}"}

    def index_tokens(self) -> dict[str, int]:
        """Map upper-cased index name -> instrument token, from Kite's instrument dump."""
        payload, _, _ = self._http_get(f"{API}/instruments/NSE", headers=self._headers())
        out = {}
        for r in csv.DictReader(io.StringIO(payload.decode("utf-8", errors="replace"))):
            if r.get("segment") == "INDICES":
                out[(r.get("tradingsymbol") or "").strip().upper()] = int(r["instrument_token"])
        return out

    def fetch_range(self, token: int, start: date, end: date) -> FetchResult:
        url = (f"{API}/instruments/historical/{token}/day"
               f"?from={start:%Y-%m-%d}&to={end:%Y-%m-%d}")
        payload, status, ctype = self._http_get(url, headers=self._headers())
        return FetchResult(payload=payload, url=url, retrieved_at=self._now(),
                           filename=f"kite_{token}_{start:%Y%m%d}_{end:%Y%m%d}.json",
                           content_type=ctype, http_status=status)

    def fetch(self, business_date: date) -> FetchResult:  # pragma: no cover
        raise NotImplementedError("Kite history is fetched per index over a range; "
                                  "use fetch_range")

    @staticmethod
    def parse(payload: bytes) -> list[dict]:
        """Kite candles -> [{business_date, open, high, low, close}]."""
        body = json.loads(payload)
        if body.get("status") != "success":
            raise FetchError(f"Kite error: {body.get('message', body)}", retryable=False)
        rows = []
        for ts, o, h, lo, c, *_ in body["data"]["candles"]:
            rows.append({"business_date": date.fromisoformat(ts[:10]), "open_level": o,
                         "high_level": h, "low_level": lo, "close_level": c})
        return rows
