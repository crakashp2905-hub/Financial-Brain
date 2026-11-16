"""Bridge prospective rows from ``paper.book`` into the canonical forward runner."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping

from financial_brain.evidence.decision_manifest import ManifestError, canonical_bytes
from financial_brain.forward_edge.market_data import executable_quote
from financial_brain.forward_edge.session_runner import ForwardSessionRunner, ForwardSessionRunnerError


class PaperBookAdapterError(ValueError):
    """A daily paper-book signal is invalid or not yet eligible for forward intake."""


@dataclass(frozen=True)
class PaperBookSignal:
    version: str
    mode: str
    signal_day: date
    rank: int
    ticker: str
    lineage: str
    signal_type: str
    horizon_sessions: int
    shares: int
    stop_price: float
    reference_price: float
    expected_net: float | None
    estimated_cost: float
    instrument_id: str

    @property
    def signal_id(self) -> str:
        identity = {"version": self.version, "mode": self.mode,
                    "signal_day": self.signal_day.isoformat(), "ticker": self.ticker}
        encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        return "paper-book:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def payload(self) -> dict[str, Any]:
        return {"adapter": "paper-book-horizon-v1", "signal_id": self.signal_id,
                "book_version": self.version, "book_mode": self.mode,
                "signal_day": self.signal_day.isoformat(), "rank": self.rank,
                "ticker": self.ticker, "lineage": self.lineage, "signal_type": self.signal_type,
                "horizon_sessions": self.horizon_sessions, "shares": self.shares,
                "stop_price": self.stop_price, "reference_price": self.reference_price,
                "expected_net": self.expected_net, "estimated_cost": self.estimated_cost,
                "instrument_id": self.instrument_id}


def pending_signals(book: Any, *, mode: str, version: str, exchange_date: date,
                    instrument_ids: Mapping[str, str]) -> tuple[PaperBookSignal, ...]:
    """Read prior-day, unentered rows; call before the legacy book's next-day ``_resolve`` step.

    Instrument mapping is explicit because a ticker alone does not identify its trading venue.
    Historical/replay rows are accepted for inspection but cannot be recorded as live decisions.
    """
    if mode != "live":
        raise PaperBookAdapterError("only live-mode paper-book rows can enter a prospective runner")
    if not version.strip() or not instrument_ids:
        raise PaperBookAdapterError("book version and explicit ticker-to-instrument mapping are required")
    rows = book.execute(
        "SELECT signal_day, rank, ticker, lineage, type, horizon, shares, stop, ref_price, expected_net, cost "
        "FROM positions WHERE version=? AND mode=? AND signal_day<? AND entry_day IS NULL "
        "AND exit_day IS NULL ORDER BY signal_day, rank, ticker", [version, mode, exchange_date]).fetchall()
    output: list[PaperBookSignal] = []
    seen: set[str] = set()
    for row in rows:
        signal_day, rank, ticker, lineage, signal_type, horizon, shares, stop, reference, net, cost = row
        symbol = str(ticker)
        instrument = instrument_ids.get(symbol)
        if not instrument or ":" not in instrument:
            raise PaperBookAdapterError(f"no explicit venue-qualified instrument mapping for {symbol}")
        if symbol in seen:
            raise PaperBookAdapterError(f"multiple pending book rows map to ticker {symbol}")
        seen.add(symbol)
        if not all((lineage, signal_type)) or int(horizon) < 1 or int(shares) < 1:
            raise PaperBookAdapterError(f"pending book row for {symbol} has invalid identity or horizon")
        numeric = (float(stop), float(reference), float(cost))
        if (not all(math.isfinite(value) and value > 0 for value in numeric[:2])
                or not math.isfinite(numeric[2]) or numeric[2] < 0):
            raise PaperBookAdapterError(f"pending book row for {symbol} has invalid prices or cost")
        expected_net = None if net is None else float(net)
        if expected_net is not None and not math.isfinite(expected_net):
            raise PaperBookAdapterError(f"pending book row for {symbol} has a non-finite expected net")
        output.append(PaperBookSignal(
            version=version, mode=mode,
            signal_day=signal_day.date() if hasattr(signal_day, "date") else
            signal_day if isinstance(signal_day, date) else date.fromisoformat(str(signal_day)),
            rank=int(rank), ticker=symbol,
            lineage=str(lineage), signal_type=str(signal_type), horizon_sessions=int(horizon),
            shares=int(shares), stop_price=numeric[0], reference_price=numeric[1],
            expected_net=expected_net, estimated_cost=numeric[2], instrument_id=instrument))
    return tuple(output)


def record_signal_decision(*, runner: ForwardSessionRunner, session_id: str,
                           signal: PaperBookSignal, quote_event_id: str,
                           decided_at: datetime, manifest: dict[str, Any] | None = None) -> str:
    """Record a live book signal against an observed quote, abstaining on unqualified alpha.

    A positive expected-net candidate requires its full canonical manifest. This function never
    issues an order: callers must still persist an allowed runner risk verdict and use a later
    executable quote through ``execute_paper_intent``.
    """
    config = runner.state_store.config(session_id)
    if signal.mode != "live" or signal.signal_day >= config.exchange_date:
        raise PaperBookAdapterError("book signal must be live-mode and precede the runner session")
    if signal.instrument_id not in config.instrument_universe:
        raise PaperBookAdapterError("book signal instrument is outside the runner's frozen universe")
    try:
        event, _ = runner.source_event(session_id, quote_event_id)
    except ForwardSessionRunnerError as exc:
        raise PaperBookAdapterError("book decision requires a journaled observed quote") from exc
    if event.kind.value != "quote" or event.instrument_id != signal.instrument_id or executable_quote(event) is None:
        raise PaperBookAdapterError("book signal requires a matching executable-quote instrument event")
    decision_id = f"{signal.signal_id}:{session_id}"
    payload = signal.payload()
    qualified = signal.expected_net is not None and signal.expected_net > 0
    if qualified:
        if manifest is None:
            raise PaperBookAdapterError("positive expected-net book signal requires a canonical decision manifest")
        try:
            canonical_bytes(manifest)
        except ManifestError as exc:
            raise PaperBookAdapterError(f"book decision manifest is invalid: {exc}") from exc
        selection = manifest["selections"]
        if (manifest["decision_id"] != decision_id or manifest["holding_sessions"] != signal.horizon_sessions
                or len(selection) != 1 or selection[0]["lineage"] != signal.lineage
                or selection[0]["symbol"] != signal.instrument_id
                or selection[0]["quantity"] != signal.shares):
            raise PaperBookAdapterError("manifest selection or horizon differs from the paper-book signal")
        runner.decide(session_id=session_id, strategy_id=signal.version, decision_id=decision_id,
                      outcome="TRADE", input_event_ids=(quote_event_id,), decided_at=decided_at,
                      payload=payload)
        runner.record_decision_manifest(session_id=session_id, decision_id=decision_id, manifest=manifest)
    else:
        payload["abstention_reason"] = "BOOK_EXPECTED_NET_NOT_POSITIVE_OR_UNCALIBRATED"
        runner.decide(session_id=session_id, strategy_id=signal.version, decision_id=decision_id,
                      outcome="NO_TRADE", input_event_ids=(quote_event_id,), decided_at=decided_at,
                      reason_codes=(payload["abstention_reason"],), payload=payload)
    return decision_id


__all__ = ["PaperBookAdapterError", "PaperBookSignal", "pending_signals", "record_signal_decision"]
