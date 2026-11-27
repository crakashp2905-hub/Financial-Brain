"""Legacy Kite account adapter; broker-side writes are permanently disabled.

``providers/kite_data.py`` remains the read-only market-data adapter. This legacy module can read the allowlisted
order/portfolio endpoints for reconciliation, but the shared :mod:`financial_brain.execution.order_policy` refuses
every non-GET request before credentials or network transport are accessed. Rollout and kill-switch checks remain
available as readiness diagnostics; passing them does not authorize physical orders.

Kite Connect v3, as documented by Zerodha (verify against the current docs before stage 1):

    POST   /orders/regular              place (form-encoded); returns {"order_id": ...}
    DELETE /orders/regular/{order_id}   cancel
    GET    /orders                      the session's orders, each with ``tag``, ``status``, ``filled_quantity``
    GET    /portfolio/positions         {"net": [...], "day": [...]}
    GET    /portfolio/holdings          delivery holdings (quantity + t1_quantity)
    GET    /quote/ltp                   last prices (served by kite_data's read-only client)

Credentials are read from the environment exactly as ``kite_data`` reads them; nothing here stores, prints or asks
for one. They cannot override the physical-order lock.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import model as M
from .broker import BrokerError, BrokerOrder, BrokerTimeout, QuoteSnapshot
from ..order_policy import deny_physical_order

API = "https://api.kite.trade"
ORDER_ENDPOINTS = ("/orders/regular", "/orders", "/portfolio/positions", "/portfolio/holdings")
MIN_ORDER_INTERVAL_S = 0.25        #: 4 orders a second, under Kite's documented 10/s; SEBI's retail threshold is lower
                                   #: for unregistered algos - see the runbook
STATUS = {"COMPLETE": M.FILLED, "CANCELLED": M.CANCELLED, "REJECTED": M.REJECTED}


class LiveLocked(RuntimeError):
    """A lock is closed; nothing was sent."""


def locks(strategy_verdict: str, *, stage_path=None, ks_path=None) -> list[str]:
    """Return rollout readiness diagnostics; an empty list never authorizes a physical order."""
    from . import killswitch as KS
    from . import rollout as RO
    closed = []
    if os.environ.get("FB_LIVE_ORDERS") != "1":
        closed.append("FB_LIVE_ORDERS is not 1 (the owner sets it; nothing in the repository does)")
    st = RO.load(stage_path) if stage_path else RO.load()
    if st["stage"] < RO.TICKET:
        closed.append(f"rollout stage is {RO.NAMES[st['stage']]}")
    elif not RO.eligible(st["stage"], strategy_verdict):
        closed.append(f"stage {RO.NAMES[st['stage']]} needs a {RO.REQUIRED_VERDICT[st['stage']]} strategy, "
                      f"this one is {strategy_verdict}")
    ks = KS.load(ks_path) if ks_path else KS.load()
    if ks.get("tripped"):
        closed.append(f"kill switch tripped: {ks.get('reason')}")
    return closed


class KiteBroker:
    name = "kite"

    def __init__(self, strategy_verdict: str, *, opener=None, stage_path=None, ks_path=None):
        self.verdict = strategy_verdict
        self._open = opener or urllib.request.urlopen     # injectable, so tests never touch the network
        self._stage_path, self._ks_path = stage_path, ks_path
        self._last = 0.0

    # ------------------------------------------------------------------ transport

    def _request(self, method: str, path: str, data: dict | None = None, timeout: int = 10) -> dict:
        if method.upper() != "GET":
            deny_physical_order(f"Kite {method.upper()} {path}")
        if not any(path == p or path.startswith(p + "/") for p in ORDER_ENDPOINTS):
            raise BrokerError(f"refusing {path!r}: not an endpoint this broker uses")
        from ...providers.kite_data import credentials
        key, token = credentials()
        body = urllib.parse.urlencode(data).encode() if data else None
        req = urllib.request.Request(f"{API}{path}", data=body, method=method, headers={
            "X-Kite-Version": "3", "Authorization": f"token {key}:{token}",
            **({"Content-Type": "application/x-www-form-urlencoded"} if body else {})})
        try:
            with self._open(req, timeout=timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raise BrokerError(f"{e.code} from {method} {path}: {e.read()[:200]!r}") from e
        except (TimeoutError, OSError) as e:
            if method == "POST":
                raise BrokerTimeout(f"{method} {path}: {e}") from e
            raise BrokerError(f"{method} {path}: {e}") from e
        out = json.loads(raw)
        if out.get("status") != "success":
            raise BrokerError(f"Kite error on {method} {path}: {out.get('message', out)}")
        return out.get("data", {})

    # ------------------------------------------------------------------ the five calls

    def place(self, order: M.Order) -> str:
        closed = locks(self.verdict, stage_path=self._stage_path, ks_path=self._ks_path)
        if closed:
            raise LiveLocked("; ".join(closed))
        wait = MIN_ORDER_INTERVAL_S - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        data = {"tradingsymbol": order.symbol, "exchange": order.exchange, "transaction_type": order.side,
                "order_type": "LIMIT", "quantity": order.qty, "price": f"{order.limit_price:.2f}",
                "product": order.product, "validity": "DAY", "tag": order.client_id}
        return str(self._request("POST", "/orders/regular", data)["order_id"])

    def cancel(self, broker_id: str) -> None:
        self._request("DELETE", f"/orders/regular/{broker_id}")

    def orders(self) -> list[BrokerOrder]:
        out = []
        for r in self._request("GET", "/orders") or []:
            filled = int(r.get("filled_quantity") or 0)
            qty = int(r.get("quantity") or 0)
            st = STATUS.get(r.get("status"))
            if st is None:                                 # OPEN, TRIGGER PENDING, ... VALIDATION PENDING
                st = M.PARTIAL if 0 < filled < qty else M.OPEN
            elif st == M.CANCELLED and filled == qty and qty:
                st = M.FILLED
            out.append(BrokerOrder(str(r["order_id"]), r.get("tag"), r.get("tradingsymbol"), r.get("exchange"),
                                   r.get("transaction_type"), qty, filled, r.get("average_price") or None, st,
                                   r.get("status_message"), r.get("exchange_update_timestamp") or r.get("order_timestamp")))
        return out

    def positions(self) -> dict[tuple[str, str], int]:
        """Delivery holdings (settled + T1) plus today's net positions, by (exchange, symbol)."""
        pos: dict[tuple[str, str], int] = {}
        for h in self._request("GET", "/portfolio/holdings") or []:
            k = (h.get("exchange", "NSE"), h["tradingsymbol"])
            pos[k] = pos.get(k, 0) + int(h.get("quantity") or 0) + int(h.get("t1_quantity") or 0)
        for p in (self._request("GET", "/portfolio/positions") or {}).get("day", []):
            k = (p.get("exchange", "NSE"), p["tradingsymbol"])
            pos[k] = pos.get(k, 0) + int(p.get("quantity") or 0)
        return {k: v for k, v in pos.items() if v}

    def ltp(self, symbols):
        from ...providers import kite_data as kd
        data = kd.quote([f"{ex}:{s}" for ex, s in symbols], kind="ltp")
        return {tuple(k.split(":", 1)): float(v["last_price"]) for k, v in data.items()}

    def quote_snapshot(self, symbols):
        """Read full quote snapshots so order checks can verify exchange and receipt freshness."""
        from ...providers import kite_data as kd

        data = kd.quote([f"{ex}:{symbol}" for ex, symbol in symbols], kind="full")
        received_at = datetime.now(timezone.utc)
        snapshots = {}
        for key, row in data.items():
            raw_stamp = row.get("timestamp")
            exchange_at = None
            if isinstance(raw_stamp, datetime):
                exchange_at = raw_stamp
            elif isinstance(raw_stamp, str) and raw_stamp.strip():
                try:
                    exchange_at = datetime.fromisoformat(raw_stamp.strip())
                except ValueError:
                    exchange_at = None
            if exchange_at is not None and (exchange_at.tzinfo is None or exchange_at.utcoffset() is None):
                exchange_at = exchange_at.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
            if exchange_at is not None:
                exchange_at = exchange_at.astimezone(timezone.utc)
            try:
                price = float(row["last_price"])
                exchange, symbol = key.split(":", 1)
            except (KeyError, TypeError, ValueError):
                continue
            snapshots[(exchange, symbol)] = QuoteSnapshot(
                price, exchange_at, received_at, "KITE_CONNECT_READ_ONLY")
        return snapshots
