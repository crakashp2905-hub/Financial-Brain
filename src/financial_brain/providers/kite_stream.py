"""Permission-gated KiteTicker stream into the canonical paper market-event path.

Ticks are normalized and kept in a bounded in-memory queue. This adapter does not archive quotes,
send orders, or turn an observed quote into a trading recommendation. Pass fresh batches to the
existing ``ForwardSessionRunner``; it applies its own frozen session permissions and accounting.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from queue import Empty, Full, Queue
from threading import Lock
from typing import Any, Callable
from zoneinfo import ZoneInfo

from financial_brain.forward_edge.clock import ClockProvider, SystemClock
from financial_brain.forward_edge.data_permissions import DataPermission, DataUse
from financial_brain.forward_edge.market_data import (
    KiteQuoteNormalizationError, executable_quote, normalize_kite_quote,
)
from financial_brain.forward_edge.market_events import EventKind, MarketEvent

SOURCE_ID = "KITE_CONNECT_READ_ONLY"
MAX_SUBSCRIPTIONS = 3000
IST = ZoneInfo("Asia/Kolkata")


class KiteStreamError(RuntimeError):
    """Kite stream could not be configured or started safely."""


def _aware_utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise KiteStreamError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _sdk_exchange_time(value: Any) -> datetime:
    """Recover the instant from KiteTicker's local-time naive ``fromtimestamp`` value."""
    if not isinstance(value, datetime):
        raise KiteQuoteNormalizationError("KiteTicker exchange timestamp is missing or malformed")
    if value.tzinfo is None or value.utcoffset() is None:
        # The official SDK builds this field with datetime.fromtimestamp(epoch), so a naive
        # value is in the host's local zone. timestamp() reverses that same local conversion.
        try:
            return datetime.fromtimestamp(value.timestamp(), timezone.utc)
        except (OSError, OverflowError, ValueError) as exc:
            raise KiteQuoteNormalizationError("KiteTicker exchange timestamp cannot be recovered") from exc
    return value.astimezone(timezone.utc)


def event_from_kite_tick(tick: dict[str, Any], *, token_to_instrument: dict[int, str],
                         received_at: datetime) -> MarketEvent:
    """Convert one official SDK full-mode tick, preserving exchange and local receipt clocks."""
    if not isinstance(tick, dict):
        raise KiteQuoteNormalizationError("KiteTicker tick must be an object")
    token = tick.get("instrument_token")
    if type(token) is not int or token not in token_to_instrument:
        raise KiteQuoteNormalizationError("KiteTicker token is outside the frozen instrument map")
    if tick.get("mode") not in (None, "full"):
        raise KiteQuoteNormalizationError("KiteTicker tick is not in full quote mode")
    timestamp = _sdk_exchange_time(tick.get("exchange_timestamp"))
    quote = {
        "timestamp": timestamp,
        "last_price": tick.get("last_price"),
        "last_quantity": tick.get("last_traded_quantity"),
        "volume": tick.get("volume_traded", 0),
        "ohlc": tick.get("ohlc") or {},
        "depth": tick.get("depth"),
    }
    return normalize_kite_quote(token_to_instrument[token], quote, received_at=received_at)


class KiteTickerStream:
    """Bounded full-mode stream for frozen underlying and option subscriptions.

    ``permission`` must be an effective, source-specific ``PAPER_SIMULATION`` grant at
    construction time. Unlicensed/unknown data cannot start the socket. Option observations
    are retained only in memory and are delivered to consumers only when fresh, two-sided,
    and positively executable. Underlying ticks may be informational and are still source-clock
    freshness-gated.
    """

    def __init__(self, *, token_to_instrument: dict[int, str], underlying_tokens: set[int],
                 option_tokens: set[int], permission: DataPermission,
                 clock: ClockProvider | None = None, queue_capacity: int = 10_000,
                 ticker_factory: Callable[..., Any] | None = None,
                 credentials_provider: Callable[[], tuple[str, str]] | None = None):
        if not isinstance(token_to_instrument, dict) or not token_to_instrument:
            raise KiteStreamError("a frozen token-to-instrument map is required")
        if any(type(token) is not int or token <= 0 for token in token_to_instrument):
            raise KiteStreamError("instrument tokens must be positive integers")
        if not underlying_tokens or not option_tokens or underlying_tokens & option_tokens:
            raise KiteStreamError("separate non-empty underlying and option token sets are required")
        requested = set(underlying_tokens) | set(option_tokens)
        if not requested <= set(token_to_instrument):
            raise KiteStreamError("every subscription must resolve in the frozen token map")
        if len(requested) > MAX_SUBSCRIPTIONS:
            raise KiteStreamError(f"Kite supports at most {MAX_SUBSCRIPTIONS} instruments per connection")
        if any(not isinstance(token_to_instrument[token], str) or ":" not in token_to_instrument[token]
               for token in requested):
            raise KiteStreamError("each subscribed instrument must be exchange-qualified")
        if any(token_to_instrument[token].split(":", 1)[0].upper() not in {"NFO", "BFO"}
               for token in option_tokens):
            raise KiteStreamError("option token identities must use NFO or BFO")
        if not isinstance(permission, DataPermission) or permission.source_id != SOURCE_ID or \
                permission.use is not DataUse.PAPER_SIMULATION:
            raise KiteStreamError("a Kite PAPER_SIMULATION permission object is required")
        self.clock = clock or SystemClock()
        now = _aware_utc(self.clock.now(), "stream clock")
        if not permission.allows(source_id=SOURCE_ID, use=DataUse.PAPER_SIMULATION,
                                 on=now.astimezone(IST).date()):
            raise KiteStreamError("effective Kite PAPER_SIMULATION permission is absent")
        if type(queue_capacity) is not int or queue_capacity < 1:
            raise KiteStreamError("queue_capacity must be a positive integer")

        self.token_to_instrument = {token: token_to_instrument[token] for token in requested}
        self.underlying_tokens = frozenset(underlying_tokens)
        self.option_tokens = frozenset(option_tokens)
        self.option_instruments = frozenset(token_to_instrument[token] for token in option_tokens)
        self.permission = permission
        self._queue: Queue[MarketEvent] = Queue(maxsize=queue_capacity)
        self._ticker_factory = ticker_factory
        self._credentials_provider = credentials_provider
        self._ticker: Any | None = None
        self._lock = Lock()
        self._state = "STOPPED"
        self._received = 0
        self._queued = 0
        self._rejected = 0
        self._stale = 0
        self._option_book_rejected = 0
        self._queue_dropped = 0
        self._last_received_at: datetime | None = None
        self._last_error: str | None = None

    def _record(self, **changes: Any) -> None:
        with self._lock:
            for key, value in changes.items():
                setattr(self, key, value)

    def _make_ticker(self):
        provider = self._credentials_provider
        if provider is None:
            from . import kite_data
            provider = kite_data.credentials
        api_key, access_token = provider()
        factory = self._ticker_factory
        if factory is None:
            try:
                from kiteconnect import KiteTicker
            except ImportError as exc:
                raise KiteStreamError("install the optional dependency with `pip install .[kite-stream]`") from exc
            factory = KiteTicker
        return factory(api_key, access_token)

    def start(self, *, threaded: bool = True) -> None:
        """Start the SDK socket and subscribe to the frozen universe in full mode."""
        if type(threaded) is not bool:
            raise KiteStreamError("threaded must be boolean")
        if not self._permission_is_effective():
            self._halt_for_expired_permission()
            raise KiteStreamError("Kite PAPER_SIMULATION permission has expired")
        if self._ticker is not None:
            raise KiteStreamError("Kite stream was already started")
        ticker = self._make_ticker()
        ticker.on_connect = self._on_connect
        ticker.on_ticks = self._on_ticks
        ticker.on_error = self._on_error
        ticker.on_close = self._on_close
        ticker.on_reconnect = self._on_reconnect
        ticker.on_noreconnect = self._on_noreconnect
        self._ticker = ticker
        self._record(_state="CONNECTING")
        ticker.connect(threaded=threaded)

    def stop(self) -> None:
        """Close the socket while keeping the SDK's reconnect loop from reviving it."""
        ticker = self._ticker
        if ticker is None:
            self._record(_state="STOPPED")
            return
        try:
            ticker.close()
        finally:
            self._record(_state="STOPPED")
            self._ticker = None

    def _permission_is_effective(self) -> bool:
        """Recheck the dated grant continuously; a socket cannot outlive its entitlement."""
        now = _aware_utc(self.clock.now(), "stream clock")
        return self.permission.allows(
            source_id=SOURCE_ID, use=DataUse.PAPER_SIMULATION, on=now.astimezone(IST).date())

    def _halt_for_expired_permission(self) -> None:
        """Close an active provider socket and fail closed after grant expiry/revocation."""
        with self._lock:
            ticker = self._ticker
            self._ticker = None
            self._state = "RIGHTS_BLOCKED"
            self._last_error = "Kite PAPER_SIMULATION permission expired or is no longer effective"
        if ticker is not None:
            try:
                ticker.close()
            except Exception as exc:  # provider SDK close failures must not revive consumption
                self._record(_last_error=f"Kite permission expired; socket close failed: {exc}")

    def _on_connect(self, ws, _response) -> None:
        tokens = list(sorted(self.token_to_instrument))
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_FULL, tokens)
        self._record(_state="CONNECTED", _last_error=None)

    def _on_ticks(self, _ws, ticks) -> None:
        if not self._permission_is_effective():
            self._halt_for_expired_permission()
            return
        received_at = _aware_utc(self.clock.now(), "tick receipt clock")
        for tick in ticks or []:
            with self._lock:
                self._received += 1
            try:
                event = event_from_kite_tick(tick, token_to_instrument=self.token_to_instrument,
                                             received_at=received_at)
            except (KiteQuoteNormalizationError, TypeError, ValueError) as exc:
                with self._lock:
                    self._rejected += 1
                    self._last_error = str(exc)
                continue
            try:
                self._queue.put_nowait(event)
            except Full:
                with self._lock:
                    self._queue_dropped += 1
                continue
            with self._lock:
                self._queued += 1
                self._last_received_at = received_at

    def _on_error(self, _ws, code, reason) -> None:
        self._record(_state="DEGRADED", _last_error=f"Kite stream error {code}: {reason}")

    def _on_close(self, _ws, code, reason) -> None:
        # Do not call ws.stop() here: the SDK uses that to disable auto-reconnect.
        self._record(_state="RECONNECTING", _last_error=f"Kite stream closed {code}: {reason}")

    def _on_reconnect(self, _ws, attempts) -> None:
        self._record(_state="RECONNECTING", _last_error=f"Kite reconnect attempt {attempts}")

    def _on_noreconnect(self, _ws) -> None:
        self._record(_state="FAILED", _last_error="Kite SDK exhausted reconnect attempts")

    def drain_fresh_events(self, *, decision_time: datetime, max_quote_age: timedelta,
                           limit: int = 1000, wait_seconds: float = 0) -> list[MarketEvent]:
        """Drain bounded batches; discard future/stale ticks and non-executable option books."""
        if not self._permission_is_effective():
            self._halt_for_expired_permission()
            return []
        decision_time = _aware_utc(decision_time, "decision_time")
        if not isinstance(max_quote_age, timedelta) or max_quote_age < timedelta(0):
            raise KiteStreamError("max_quote_age must be a non-negative timedelta")
        if type(limit) is not int or limit < 1:
            raise KiteStreamError("limit must be a positive integer")
        if not isinstance(wait_seconds, (int, float)) or not 0 <= wait_seconds <= 60:
            raise KiteStreamError("wait_seconds must be between zero and 60")
        fresh = []
        for index in range(limit):
            try:
                event = self._queue.get(timeout=wait_seconds if index == 0 else 0)
            except Empty:
                break
            if event.available_at > decision_time or event.event_time > decision_time or \
                    decision_time - event.event_time > max_quote_age:
                with self._lock:
                    self._stale += 1
                continue
            if event.instrument_id in self.option_instruments and (
                    event.kind is not EventKind.QUOTE or executable_quote(event) is None):
                with self._lock:
                    self._option_book_rejected += 1
                continue
            fresh.append(event)
        return fresh

    def ingest_into(self, runner, *, session_id: str, config, decision_time: datetime,
                    limit: int = 1000, wait_seconds: float = 0) -> dict[str, int]:
        """Pass a fresh event batch into the existing runner's durable, permission-checked path."""
        max_age_ms = config.constraints.get("max_quote_age_ms", 2_000)
        if type(max_age_ms) is not int or max_age_ms < 0:
            raise KiteStreamError("frozen run max_quote_age_ms must be a non-negative integer")
        events = self.drain_fresh_events(
            decision_time=decision_time, max_quote_age=timedelta(milliseconds=max_age_ms),
            limit=limit, wait_seconds=wait_seconds)
        if not events:
            return {"received": 0, "persisted": 0, "duplicate": 0}
        return runner.ingest(session_id, config, events)

    def health(self) -> dict[str, Any]:
        """Return operational counters and an explicit fail-closed entry readiness state."""
        with self._lock:
            no_entry = (self._state != "CONNECTED" or self._queue_dropped > 0 or
                        self._stale > 0 or self._rejected > 0 or
                        self._option_book_rejected > 0 or self._state == "RIGHTS_BLOCKED")
            reasons = []
            if self._state != "CONNECTED":
                reasons.append(f"stream_state_{self._state.lower()}")
            if self._state == "RIGHTS_BLOCKED":
                reasons.append("data_permission_expired")
            if self._queue_dropped:
                reasons.append("queue_events_dropped")
            if self._stale:
                reasons.append("stale_events_discarded")
            if self._rejected:
                reasons.append("malformed_events_rejected")
            if self._option_book_rejected:
                reasons.append("non_executable_option_books_rejected")
            return {"source": SOURCE_ID, "state": self._state,
                    "entry_readiness": "NO_ENTRY" if no_entry else "READY",
                    "evidence_quality": "DEGRADED" if no_entry else "STREAM_HEALTHY_UNATTESTED",
                    "no_entry_reasons": reasons,
                    "subscribed_instruments": len(self.token_to_instrument),
                    "underlying_subscriptions": len(self.underlying_tokens),
                    "option_subscriptions": len(self.option_tokens),
                    "queue_depth": self._queue.qsize(), "received": self._received,
                    "queued": self._queued, "rejected": self._rejected,
                    "stale": self._stale, "option_book_rejected": self._option_book_rejected,
                    "queue_dropped": self._queue_dropped,
                    "last_received_at": self._last_received_at.isoformat()
                    if self._last_received_at else None,
                    "last_error": self._last_error}


__all__ = ["KiteStreamError", "KiteTickerStream", "event_from_kite_tick"]
