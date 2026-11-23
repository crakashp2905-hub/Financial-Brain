"""Normalize the existing Kite read-only quote runner into forward-edge market events.

Kite quote packets expose an exchange `timestamp`, last trade time, and bid/ask depth.
The event clock uses the quote packet timestamp; receipt and availability use the local
observation clock. A missing exchange timestamp or two-sided book is rejected rather than
replaced with the receipt time.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo
from pathlib import Path

from financial_brain.forward_edge.market_events import EventKind, MarketEvent
from financial_brain.forward_edge.quote_archive import QuoteArchive
from financial_brain.forward_edge.clock import ClockProvider, SystemClock, capture_market_clocks

IST = ZoneInfo("Asia/Kolkata")


class KiteQuoteNormalizationError(ValueError):
    """Kite quote did not contain trustworthy clocks and a two-sided top of book."""


def executable_quote(event: MarketEvent) -> dict[str, Any] | None:
    """Return fill inputs only for a complete, positive-liquidity two-sided quote event.

    Informational ticks and quotes without depth/quantity stay available for analysis but
    cannot be mistaken for executable prices.
    """
    if event.kind is not EventKind.QUOTE:
        return None
    payload = event.payload
    try:
        bid = Decimal(str(payload["bid"]))
        ask = Decimal(str(payload["ask"]))
        bid_quantity = int(payload["bid_quantity"])
        ask_quantity = int(payload["ask_quantity"])
        volume = int(payload["volume"])
    except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError):
        return None
    depth = payload.get("depth")
    if (not bid.is_finite() or not ask.is_finite() or bid <= 0 or ask <= bid or
            bid_quantity <= 0 or ask_quantity <= 0 or volume <= 0 or
            not isinstance(depth, dict) or not depth.get("buy") or not depth.get("sell")):
        return None

    def level(row: Any) -> tuple[Decimal, int]:
        if isinstance(row, dict):
            raw_price, raw_quantity = row["price"], row["quantity"]
        elif isinstance(row, (list, tuple)) and len(row) == 2:
            raw_price, raw_quantity = row
        else:
            raise ValueError("invalid depth level")
        return Decimal(str(raw_price)), int(raw_quantity)

    try:
        bid_levels = [level(row) for row in depth["buy"]]
        ask_levels = [level(row) for row in depth["sell"]]
    except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError):
        return None
    if (any(not price.is_finite() or price <= 0 or quantity <= 0 for price, quantity in bid_levels + ask_levels)
            or any(bid_levels[index][0] < bid_levels[index + 1][0] for index in range(len(bid_levels) - 1))
            or any(ask_levels[index][0] > ask_levels[index + 1][0] for index in range(len(ask_levels) - 1))
            or bid_levels[0] != (bid, bid_quantity) or ask_levels[0] != (ask, ask_quantity)):
        return None
    try:
        lower = Decimal(str(payload["lower_circuit_limit"])) if payload.get("lower_circuit_limit") is not None else None
        upper = Decimal(str(payload["upper_circuit_limit"])) if payload.get("upper_circuit_limit") is not None else None
    except InvalidOperation:
        return None
    if ((lower is not None and (not lower.is_finite() or lower <= 0 or bid <= lower))
            or (upper is not None and (not upper.is_finite() or upper <= 0 or ask >= upper))):
        return None
    status = str(payload.get("exchange_status", "")).strip().upper()
    phase = str(payload.get("session_phase", "")).strip().upper()
    if (status and status not in {"OPEN", "NORMAL", "CONTINUOUS", "TRADING"}) or \
            (phase and phase != "CONTINUOUS"):
        return None
    return {"bid": bid, "ask": ask, "bid_quantity": bid_quantity,
            "ask_quantity": ask_quantity, "volume": volume,
            "depth": depth, "bid_levels": tuple(bid_levels), "ask_levels": tuple(ask_levels),
            "source": event.source, "event_id": event.event_id,
            "event_time": event.event_time, "received_at": event.received_at,
            "available_at": event.available_at}


def _exchange_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError as exc:
            raise KiteQuoteNormalizationError("Kite exchange timestamp is malformed") from exc
    else:
        raise KiteQuoteNormalizationError("Kite exchange timestamp is missing")
    # Kite's REST response serializes exchange time without an offset; interpret that
    # documented exchange-local clock as India Standard Time. Never infer from receipt.
    return parsed.replace(tzinfo=IST) if parsed.tzinfo is None else parsed


def normalize_kite_quote(instrument: str, quote: dict[str, Any], *,
                         received_at: datetime | None = None,
                         clock: ClockProvider | None = None,
                         availability_delay: timedelta = timedelta(0),
                         max_future_skew: timedelta = timedelta(seconds=2)) -> MarketEvent:
    """Convert one full Kite quote to a source-labeled event with explicit clocks."""
    if not isinstance(availability_delay, timedelta) or availability_delay < timedelta(0):
        raise KiteQuoteNormalizationError("availability delay cannot be negative")
    if not isinstance(max_future_skew, timedelta) or max_future_skew < timedelta(0):
        raise KiteQuoteNormalizationError("maximum future skew must be a non-negative duration")
    try:
        venue, symbol = instrument.split(":", 1)
    except ValueError as exc:
        raise KiteQuoteNormalizationError("instrument must be EXCHANGE:TRADINGSYMBOL") from exc
    if not venue.strip() or not symbol.strip():
        raise KiteQuoteNormalizationError("instrument venue and symbol are required")
    timestamp = _exchange_timestamp(quote.get("timestamp"))
    if received_at is None:
        try:
            clocks = capture_market_clocks(exchange_at=timestamp, provider=clock or SystemClock(),
                                           availability_delay=availability_delay)
        except ValueError as exc:
            raise KiteQuoteNormalizationError(str(exc)) from exc
        received_at, available_at = clocks.received_at, clocks.available_at
    else:
        if received_at.tzinfo is None or received_at.utcoffset() is None:
            raise KiteQuoteNormalizationError("received_at must be timezone aware")
        available_at = received_at + availability_delay
    if timestamp.astimezone(received_at.tzinfo) > received_at + max_future_skew:
        raise KiteQuoteNormalizationError(
            f"Kite exchange timestamp exceeds the configured future-skew tolerance of {max_future_skew}"
        )
    try:
        last_price = float(quote["last_price"])
        volume = int(quote.get("volume", 0))
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise KiteQuoteNormalizationError("Kite quote contains invalid last price or volume") from exc
    if not math.isfinite(last_price) or last_price <= 0 or volume < 0:
        raise KiteQuoteNormalizationError("Kite quote last price must be positive and volume non-negative")
    depth = quote.get("depth")
    bids = depth.get("buy") if isinstance(depth, dict) else None
    asks = depth.get("sell") if isinstance(depth, dict) else None
    bid_rows = bids if isinstance(bids, list) else []
    ask_rows = asks if isinstance(asks, list) else []
    executable = bool(bid_rows) and bool(ask_rows)
    kind = EventKind.QUOTE if executable else EventKind.TICK
    if executable:
        try:
            bid, ask = float(bid_rows[0]["price"]), float(ask_rows[0]["price"])
            payload: dict[str, Any] = {
                "bid": bid, "ask": ask,
                "bid_quantity": int(bid_rows[0].get("quantity", 0)),
                "ask_quantity": int(ask_rows[0].get("quantity", 0)),
                "last_price": last_price,
                "volume": volume,
                "depth": {"buy": bid_rows, "sell": ask_rows},
            }
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise KiteQuoteNormalizationError("Kite quote contains invalid depth") from exc
    else:
        payload = {"price": last_price, "volume": volume,
                   "non_executable_reason": "TWO_SIDED_DEPTH_UNAVAILABLE"}
        if quote.get("last_quantity") is not None:
            try:
                payload["quantity"] = int(quote["last_quantity"])
            except (TypeError, ValueError, OverflowError) as exc:
                raise KiteQuoteNormalizationError("Kite last-trade quantity is invalid") from exc
    ohlc = quote.get("ohlc") or {}
    if isinstance(ohlc, dict):
        for source_key, payload_key in (("open", "session_open"), ("high", "session_high"),
                                        ("low", "session_low"), ("close", "previous_close")):
            if ohlc.get(source_key) is not None:
                try:
                    value = float(ohlc[source_key])
                except (TypeError, ValueError, OverflowError) as exc:
                    raise KiteQuoteNormalizationError(f"Kite OHLC {source_key} is invalid") from exc
                if not math.isfinite(value) or value <= 0:
                    raise KiteQuoteNormalizationError(f"Kite OHLC {source_key} must be positive and finite")
                payload[payload_key] = value
    for key in ("lower_circuit_limit", "upper_circuit_limit"):
        if quote.get(key) is not None:
            payload[key] = float(quote[key])
    exchange_status = quote.get("exchange_status", quote.get("market_status"))
    if exchange_status is not None:
        payload["exchange_status"] = str(exchange_status)
    if quote.get("session_phase") is not None:
        payload["session_phase"] = str(quote["session_phase"])
    packet_hash = hashlib.sha256(json.dumps(quote, sort_keys=True, default=str,
                                             separators=(",", ":")).encode("utf-8")).hexdigest()
    receipt_iso = received_at.isoformat()
    event_id = f"kite:{venue.upper()}:{symbol}:{timestamp.isoformat()}:{receipt_iso}:{packet_hash[:16]}"
    return MarketEvent(event_id=event_id, kind=kind, venue=venue.upper(),
                       instrument_id=f"{venue.upper()}:{symbol}", event_time=timestamp,
                       received_at=received_at, available_at=available_at, payload=payload,
                       source="KITE_CONNECT_READ_ONLY", evidence_id=packet_hash,
                       source_at=timestamp, exchange_at=timestamp)


def archive_kite_quote_batch(archive: QuoteArchive, quotes: dict[str, dict[str, Any]], *,
                             received_at: datetime | None = None,
                             clock: ClockProvider | None = None) -> dict[str, int]:
    """Append normalized observations from one existing Kite quote response batch."""
    counts = {"received": len(quotes), "archived": 0, "rejected": 0}
    if received_at is None and quotes:
        received_at = (clock or SystemClock()).now()
    for instrument, quote in sorted(quotes.items()):
        try:
            event = normalize_kite_quote(instrument, quote, received_at=received_at)
            archive.append(event)
            counts["archived"] += 1
        except (KiteQuoteNormalizationError, ValueError, TypeError):
            counts["rejected"] += 1
    return counts


def archive_kite_quote_batch_to_path(path: Path, quotes: dict[str, dict[str, Any]], *,
                                    received_at: datetime | None = None,
                                    clock: ClockProvider | None = None) -> dict[str, int]:
    """Persist one poll batch durably without retaining a file handle across runner exits."""
    archive = QuoteArchive(path, clock=clock)
    try:
        return archive_kite_quote_batch(archive, quotes, received_at=received_at, clock=clock)
    finally:
        archive.close()


# ---- merged from main (2026-11-18): definitions main's modules depend on; the branch's own
# ---- definitions above are unchanged.
from dataclasses import dataclass
from datetime import date, timezone


class DataError(ValueError):
    pass


KINDS = frozenset({"TICK", "QUOTE", "DEPTH", "BAR", "HALT", "AUCTION", "CORP_ACTION", "BROKER_UPDATE"})


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DataError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class NormalizedEvent:
    kind: str
    instrument: str
    event_utc: datetime
    received_utc: datetime
    available_utc: datetime
    payload: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise DataError(f"unknown kind {self.kind}")
        e = _aware(self.event_utc, "event")
        r = _aware(self.received_utc, "received")
        a = _aware(self.available_utc, "available")
        if not (e <= r <= a):
            raise DataError("clocks must satisfy event <= received <= available")

    def content_sha256(self) -> str:
        body = json.dumps({"kind": self.kind, "instrument": self.instrument,
                           "event": _aware(self.event_utc, "e").isoformat(),
                           "received": _aware(self.received_utc, "r").isoformat(),
                           "available": _aware(self.available_utc, "a").isoformat(),
                           "payload": [list(p) for p in self.payload]}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


def decision_visible(event: NormalizedEvent, decision_utc: datetime) -> bool:
    """An event may inform a decision only if it was available then (not merely event-stamped then)."""
    return _aware(event.available_utc, "a") <= _aware(decision_utc, "decision")


@dataclass(frozen=True)
class Calendar:
    version: str
    holidays: frozenset[date]
    special_sessions: frozenset[date] = frozenset()

    def is_tradable(self, day: date) -> bool:
        if day in self.special_sessions:
            return True
        return day.weekday() < 5 and day not in self.holidays

    def next_tradable(self, after: date) -> date:
        day = after + timedelta(days=1)
        for _ in range(60):
            if self.is_tradable(day):
                return day
            day += timedelta(days=1)
        raise DataError("no tradable session within 60 days: calendar is wrong")


def load_calendar(path: Path) -> Calendar:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "version" not in data or "holidays" not in data:
        raise DataError("calendar needs version and holidays")
    return Calendar(version=str(data["version"]),
                    holidays=frozenset(date.fromisoformat(d) for d in data["holidays"]),
                    special_sessions=frozenset(date.fromisoformat(d) for d in data.get("special_sessions", [])))


def fixture_feed(path: Path) -> list[NormalizedEvent]:
    """Replay normalized events from a JSON fixture, in (event, sequence) order."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    events = []
    for row in rows:
        events.append(NormalizedEvent(
            kind=row["kind"], instrument=row["instrument"],
            event_utc=datetime.fromisoformat(row["event_utc"]),
            received_utc=datetime.fromisoformat(row["received_utc"]),
            available_utc=datetime.fromisoformat(row["available_utc"]),
            payload=tuple(sorted((k, str(v)) for k, v in row.get("payload", {}).items())),
        ))
    return sorted(events, key=lambda e: (e.event_utc, e.instrument, e.kind))




@dataclass(frozen=True)
class FeedHealth:
    last_available_utc: datetime | None
    gaps: int
    disconnects: int
    dropped_updates: int


def entry_allowed(health: FeedHealth, now: datetime, *, max_age: timedelta = timedelta(seconds=5)) -> tuple[bool, list[str]]:
    reasons = []
    if health.last_available_utc is None:
        reasons.append("NO_QUOTES")
    elif _aware(now, "now") - _aware(health.last_available_utc, "last") > max_age:
        reasons.append("STALE_QUOTE")
    if health.gaps:
        reasons.append("QUOTE_GAP")
    if health.disconnects:
        reasons.append("DISCONNECTED")
    if health.dropped_updates:
        reasons.append("DROPPED_UPDATES")
    return (not reasons), reasons


@dataclass(frozen=True)
class DecisionSnapshot:
    decision_utc: datetime
    inputs: tuple[tuple[str, str], ...]  # (name, sha256) for every input the strategy read

    def sha256(self) -> str:
        body = json.dumps({"t": _aware(self.decision_utc, "d").isoformat(), "inputs": [list(i) for i in self.inputs]},
                          sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Instrument:
    isin: str
    symbol: str
    lot: int
    tick: str
    status: str  # ACTIVE, SUSPENDED, DELISTED
    rights_ok: bool
    expiry: date | None = None


def instrument_gate(inst: Instrument, as_of: date) -> list[str]:
    reasons = []
    if inst.status != "ACTIVE":
        reasons.append("NOT_TRADABLE_STATUS")
    if inst.expiry is not None and inst.expiry <= as_of:
        reasons.append("EXPIRED")
    if inst.lot <= 0:
        reasons.append("BAD_LOT")
    if not inst.rights_ok:
        reasons.append("NO_DATA_RIGHTS")
    return reasons


def option_quote_executable(bid: float | None, ask: float | None, bid_qty: int, ask_qty: int) -> tuple[bool, str]:
    if bid is None or ask is None:
        return False, "ONE_SIDED_OR_MID_ONLY"
    if bid <= 0 or ask <= 0 or ask < bid:
        return False, "CROSSED_OR_INVALID"
    if bid_qty <= 0 or ask_qty <= 0:
        return False, "NO_DEPTH"
    return True, "EXECUTABLE"


def reconcile_close(live_close: float, authoritative_close: float, *, tolerance: float = 0.0) -> dict:
    """Record divergence as data. Nothing is overwritten."""
    diff = authoritative_close - live_close
    status = "MATCH" if abs(diff) <= tolerance else "DIVERGED"
    return {"status": status, "live_close": live_close, "authoritative_close": authoritative_close,
            "difference": round(diff, 10), "version": "close-recon-v1"}


def data_gate(*, age_s: float, spread_bps: float | None, volume: int, circuit_hit: bool, frozen: bool,
              auction_seen: bool, max_age_s: float = 5.0, max_spread_bps: float = 50.0) -> list[str]:
    codes = []
    if age_s > max_age_s:
        codes.append("STALE")
    if spread_bps is None or spread_bps > max_spread_bps:
        codes.append("WIDE_SPREAD")
    if volume <= 0:
        codes.append("ZERO_VOLUME")
    if circuit_hit:
        codes.append("CIRCUIT")
    if frozen:
        codes.append("FREEZE")
    if not auction_seen:
        codes.append("MISSING_AUCTION")
    return sorted(codes)


def feature_at(closes: list[float], timestamps: list[datetime], t: datetime, window: int = 5) -> float | None:
    """Mean of the last ``window`` closes whose timestamp is <= t. Future bars cannot change the value."""
    idx = [i for i, ts in enumerate(timestamps) if _aware(ts, "bar") <= _aware(t, "t")]
    if len(idx) < window:
        return None
    last = idx[-window:]
    return sum(closes[i] for i in last) / window


def coverage(sessions: dict[str, int], names: dict[str, bool], intents: list[tuple[float, bool]],
             min_quotes: int = 1) -> dict[str, float]:
    """Share of sessions with enough quotes, share of names with coverage, and share of intent notional covered."""
    total_sessions = len(sessions)
    good_sessions = sum(1 for n in sessions.values() if n >= min_quotes)
    total_names = len(names)
    good_names = sum(1 for ok in names.values() if ok)
    total_notional = sum(n for n, _ in intents)
    covered = sum(n for n, ok in intents if ok)
    return {
        "sessions_pct": round(100.0 * good_sessions / total_sessions, 4) if total_sessions else 0.0,
        "names_pct": round(100.0 * good_names / total_names, 4) if total_names else 0.0,
        "intent_notional_pct": round(100.0 * covered / total_notional, 4) if total_notional else 0.0,
    }
