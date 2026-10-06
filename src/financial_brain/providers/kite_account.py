"""The owner's own Kite account, read only: holdings, positions, and today's trade book.

Separate from ``kite_data`` on purpose. That module serves market data and its tests forbid any account path; this
one serves exactly three account paths, matched **exactly** (not by prefix, so no longer path can ride through), all
GET. Nothing here can create, modify or cancel anything: there is no write verb in the file and no transactional
path in the allowlist. The data is the owner's; it is stored only under ``data/`` (gitignored) and sent nowhere.

Kite's trade book returns only the current day's trades, so the journal (``portfolio/journal``) accumulates it day
by day from the evening refresh.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import kite_data as kd

#: The complete set of paths this module will request. Exact matches, GET only.
ACCOUNT_ENDPOINTS = frozenset({"/portfolio/holdings", "/portfolio/positions", "/trades"})


class KiteAccountError(RuntimeError):
    """Kite refused, or a path outside the allowlist was asked for. Never carries a credential."""


def _get(path: str, *, timeout: int = 30) -> list | dict:
    if path not in ACCOUNT_ENDPOINTS:
        raise KiteAccountError(f"refusing {path!r}: not one of the account read endpoints")
    key, token = kd.credentials()
    req = urllib.request.Request(f"{kd.API}{path}", method="GET", headers={
        "X-Kite-Version": "3", "Authorization": f"token {key}:{token}"})
    kd._throttle()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise KiteAccountError(f"{e.code} from {path}: {e.read()[:200]!r}") from e
    except OSError as e:
        raise KiteAccountError(f"{path}: {e}") from e
    if body.get("status") != "success":
        raise KiteAccountError(f"Kite error on {path}: {body.get('message', body)}")
    return body["data"]


def holdings() -> list[dict]:
    """Delivery holdings: symbol, ISIN, quantity, average price, last price, P&L."""
    return _get("/portfolio/holdings")


def positions() -> dict:
    """Open positions: {'net': [...], 'day': [...]}."""
    return _get("/portfolio/positions")


def trades() -> list[dict]:
    """Today's executed trades (Kite returns the current day only)."""
    return _get("/trades")
