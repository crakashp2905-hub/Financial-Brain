"""Bridge prospective rows from ``paper.book`` into the canonical forward runner."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, cast

from financial_brain.evidence.decision_manifest import ManifestError, canonical_bytes
from financial_brain.forward_edge.market_data import executable_quote
from financial_brain.forward_edge.market_events import MarketEvent
from financial_brain.forward_edge.session_runner import ForwardSessionRunner, ForwardSessionRunnerError
from financial_brain.kernel.units import BasisPoints, Price, UnitError, WholeUnits


class PaperBookAdapterError(ValueError):
    """A daily paper-book signal is invalid or not yet eligible for forward intake."""


BRIDGE_SCHEMA = """CREATE TABLE IF NOT EXISTS paper_book_bridge (
    version VARCHAR NOT NULL, mode VARCHAR NOT NULL, signal_day DATE NOT NULL, ticker VARCHAR NOT NULL,
    signal_id VARCHAR NOT NULL, session_id VARCHAR NOT NULL, order_id VARCHAR NOT NULL,
    state VARCHAR NOT NULL, requested_shares INTEGER NOT NULL, filled_shares INTEGER NOT NULL,
    receipt_sha256 VARCHAR NOT NULL, updated_at TIMESTAMP NOT NULL, simulated_charges_inr DOUBLE,
    PRIMARY KEY (signal_id, session_id, order_id))"""


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
    shares: int | WholeUnits
    stop_price: float | Decimal | Price
    reference_price: float | Decimal | Price
    expected_net: float | BasisPoints | None
    estimated_cost: float | BasisPoints
    instrument_id: str

    def __post_init__(self) -> None:
        try:
            quantity = (self.shares if isinstance(self.shares, WholeUnits)
                        else WholeUnits(Decimal(str(self.shares))))
            if quantity.units <= 0:
                raise PaperBookAdapterError("paper-book shares must be positive whole units")
            object.__setattr__(self, "shares", int(quantity.units))
            for name in ("stop_price", "reference_price"):
                value = getattr(self, name)
                price = value if isinstance(value, Price) else Price(Decimal(str(value)), "INR")
                if price.currency != "INR":
                    raise PaperBookAdapterError(f"paper-book {name} must be denominated in INR")
                object.__setattr__(self, name, float(price.value))
            if isinstance(self.expected_net, BasisPoints):
                object.__setattr__(self, "expected_net", float(self.expected_net.as_rate()))
            if isinstance(self.estimated_cost, BasisPoints):
                object.__setattr__(self, "estimated_cost", float(self.estimated_cost.as_rate()))
            expected = cast(float | None, self.expected_net)
            cost = cast(float, self.estimated_cost)
            if (expected is not None and not math.isfinite(expected)
                    or not math.isfinite(cost) or cost < 0):
                raise PaperBookAdapterError("paper-book expected-net and cost rates must be finite")
        except (UnitError, InvalidOperation, TypeError, ValueError) as exc:
            if isinstance(exc, PaperBookAdapterError):
                raise
            raise PaperBookAdapterError(f"paper-book signal has invalid typed values: {exc}") from exc

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


@dataclass(frozen=True)
class PaperBookExit:
    signal: PaperBookSignal
    reason: str
    completed_sessions_held: int
    quote_event_id: str
    bid_price: float


def _simulated_charges(receipt: dict[str, Any], order_id: str) -> float | None:
    """Validate receipt line items; missing schedule evidence stays explicitly unpriced."""
    raw_total = receipt.get("charges_inr")
    items = receipt.get("cost_items")
    if raw_total is None:
        if items is not None:
            raise PaperBookAdapterError("runner receipt has cost items without a charge total")
        return None
    if not isinstance(items, list) or not items:
        raise PaperBookAdapterError("runner receipt charge total lacks itemized cost evidence")
    try:
        total = Decimal(str(raw_total))
        item_total = Decimal(0)
        for item in items:
            line_total = Decimal(str(item["total"]))
            if item.get("order_id") != order_id or not line_total.is_finite() or line_total < 0:
                raise ValueError("cost line has invalid order identity or amount")
            item_total += line_total
    except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
        raise PaperBookAdapterError(f"runner receipt has invalid itemized charges: {exc}") from exc
    if not total.is_finite() or total < 0 or item_total != total:
        raise PaperBookAdapterError("runner receipt charge total differs from its line items")
    return float(total)


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
    blocked_ids: set[str] = set()
    if any(str(row[0]) == "paper_book_bridge" for row in book.execute("SHOW TABLES").fetchall()):
        blocked_ids = {str(row[0]) for row in book.execute(
            "SELECT DISTINCT signal_id FROM paper_book_bridge WHERE state LIKE 'PARTIAL%'").fetchall()}
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
    return tuple(signal for signal in output if signal.signal_id not in blocked_ids)


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
    expected_net = cast(float | None, signal.expected_net)
    qualified = expected_net is not None and expected_net > 0
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


def reconcile_entry_receipt(*, book: Any, runner: ForwardSessionRunner, session_id: str,
                            signal: PaperBookSignal, decision_id: str, order_id: str,
                            receipt: dict[str, Any]) -> str:
    """Idempotently mirror a runner entry receipt into the DuckDB daily book.

    Full fills update the book entry marks. Missed/rejected attempts remain retryable.
    Partial fills are recorded visibly and block automatic retries until an operator handles
    the residual quantity; this prevents duplicate exposure between the two ledgers.
    """
    records = runner.records(session_id)
    stored = next((item for item in records["execution_receipts"] if item.get("order_id") == order_id), None)
    intent = next((item for item in records["intents"] if item.get("order_id") == order_id), None)
    decision = next((item for item in records["decisions"] if item.get("decision_id") == decision_id), None)
    if stored != receipt or intent is None or intent.get("decision_id") != decision_id or \
            intent.get("side") != "BUY" or decision is None or \
            decision.get("payload", {}).get("signal_id") != signal.signal_id:
        raise PaperBookAdapterError("runner receipt, intent and book decision do not share one signal identity")
    status = str(receipt.get("status", ""))
    filled = int(receipt.get("filled_quantity", -1))
    requested = int(receipt.get("requested_quantity", -1))
    if requested != signal.shares or not 0 <= filled <= requested:
        raise PaperBookAdapterError("runner receipt quantity differs from the paper-book signal")
    if status == "FILLED":
        if filled != requested or receipt.get("average_price") is None:
            raise PaperBookAdapterError("FILLED runner receipt has incomplete quantity or price")
        state = "FILLED"
    elif status == "PARTIAL":
        if not 0 < filled < requested:
            raise PaperBookAdapterError("PARTIAL runner receipt has inconsistent filled quantity")
        state = "PARTIAL_REVIEW"
    elif status in {"MISSED", "REJECTED"}:
        if filled != 0:
            raise PaperBookAdapterError("unfilled runner receipt reports a non-zero fill")
        state = "NO_FILL"
    else:
        raise PaperBookAdapterError(f"unsupported entry receipt state {status!r}")
    encoded = json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    charges = _simulated_charges(receipt, order_id)
    config = runner.state_store.config(session_id)
    occurred = datetime.fromisoformat(str(receipt["simulated_at"]).replace("Z", "+00:00"))
    book.execute(BRIDGE_SCHEMA)
    book.execute("ALTER TABLE paper_book_bridge ADD COLUMN IF NOT EXISTS simulated_charges_inr DOUBLE")
    book.execute("BEGIN TRANSACTION")
    try:
        previous = book.execute(
            "SELECT state, receipt_sha256 FROM paper_book_bridge "
            "WHERE signal_id=? AND session_id=? AND order_id=?",
            [signal.signal_id, session_id, order_id]).fetchone()
        if previous:
            if previous[1] != digest:
                raise PaperBookAdapterError("runner order receipt conflicts with prior book reconciliation")
            book.execute("COMMIT")
            return str(previous[0])
        if state == "FILLED":
            result = book.execute(
                "UPDATE positions SET entry_day=?, entry_px=?, last_day=?, last_px=?, held=0 "
                "WHERE version=? AND mode='live' AND signal_day=? AND ticker=? "
                "AND entry_day IS NULL AND exit_day IS NULL RETURNING ticker",
                [config.exchange_date, float(receipt["average_price"]), config.exchange_date,
                 float(receipt["average_price"]), signal.version, signal.signal_day, signal.ticker]).fetchone()
            if result is None:
                raise PaperBookAdapterError("unentered live book row is missing or already reconciled")
        book.execute("INSERT INTO paper_book_bridge (version,mode,signal_day,ticker,signal_id,session_id,order_id,"
                     "state,requested_shares,filled_shares,receipt_sha256,updated_at,simulated_charges_inr) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     [signal.version, signal.mode, signal.signal_day, signal.ticker, signal.signal_id,
                      session_id, order_id, state, requested, filled, digest, occurred, charges])
        book.execute("COMMIT")
        return state
    except Exception:
        book.execute("ROLLBACK")
        raise


def due_exits(book: Any, *, runner: ForwardSessionRunner, session_id: str,
              mode: str, version: str, exchange_date: date,
              completed_sessions_held: Mapping[str, int],
              instrument_ids: Mapping[str, str],
              quote_events: Mapping[str, MarketEvent]) -> tuple[PaperBookExit, ...]:
    """Mark runner-owned open positions and return stop/time exits from observed quotes.

    ``completed_sessions_held`` is supplied by the sourced exchange-calendar scheduler. Calendar
    days are never substituted for trading sessions. Missing/non-executable quote evidence fails
    the whole mark batch so a data gap cannot silently turn into a time exit.
    """
    config = runner.state_store.config(session_id)
    if config.exchange_date != exchange_date:
        raise PaperBookAdapterError("exit mark date differs from the forward-session date")
    rows = book.execute(
        "SELECT p.signal_day,p.rank,p.ticker,p.lineage,p.type,p.horizon,p.shares,p.stop,p.ref_price,"
        "p.expected_net,p.cost,p.entry_day,p.held,p.last_day "
        "FROM positions p WHERE p.mode=? AND p.version=? AND p.entry_day IS NOT NULL "
        "AND p.exit_day IS NULL AND EXISTS (SELECT 1 FROM paper_book_bridge b "
        "WHERE b.version=p.version AND b.mode=p.mode AND b.signal_day=p.signal_day AND b.ticker=p.ticker "
        "AND b.state='FILLED') ORDER BY p.entry_day,p.rank,p.ticker", [mode, version]).fetchall()
    output: list[PaperBookExit] = []
    marks: list[tuple[PaperBookSignal, int, MarketEvent, float, str | None]] = []
    for row in rows:
        (signal_day, rank, ticker, lineage, signal_type, horizon, shares, stop, reference, net, cost,
         entry_day, prior_held, prior_mark_day) = row
        symbol = str(ticker)
        instrument = instrument_ids.get(symbol)
        if not instrument or instrument not in quote_events:
            raise PaperBookAdapterError(f"runner-owned position {symbol} lacks its current executable quote")
        signal = PaperBookSignal(
            version=version, mode=mode,
            signal_day=signal_day.date() if hasattr(signal_day, "date") else
            signal_day if isinstance(signal_day, date) else date.fromisoformat(str(signal_day)),
            rank=int(rank), ticker=symbol, lineage=str(lineage), signal_type=str(signal_type),
            horizon_sessions=int(horizon), shares=int(shares), stop_price=float(stop),
            reference_price=float(reference), expected_net=None if net is None else float(net),
            estimated_cost=float(cost), instrument_id=instrument)
        held = completed_sessions_held.get(signal.signal_id)
        if type(held) is not int or held < 0:
            raise PaperBookAdapterError(f"completed trading-session age is missing for {symbol}")
        entry_day = entry_day.date() if hasattr(entry_day, "date") else entry_day
        prior_mark_day = prior_mark_day.date() if hasattr(prior_mark_day, "date") else prior_mark_day
        if exchange_date < entry_day or (prior_mark_day is not None and exchange_date < prior_mark_day):
            raise PaperBookAdapterError(f"session mark date moves backwards for {symbol}")
        if prior_held is not None and held < int(prior_held):
            raise PaperBookAdapterError(f"completed session age moves backwards for {symbol}")
        event = quote_events[instrument]
        try:
            journaled_event, _ = runner.source_event(session_id, event.event_id)
        except ForwardSessionRunnerError as exc:
            raise PaperBookAdapterError(f"current quote for {symbol} is not in the runner journal") from exc
        if journaled_event != event:
            raise PaperBookAdapterError(f"current quote for {symbol} differs from its journaled event")
        quote = executable_quote(event)
        if event.instrument_id != instrument or quote is None:
            raise PaperBookAdapterError(f"current quote is not executable for runner-owned position {symbol}")
        bid = float(quote["bid"])
        last = float(event.payload["last_price"])
        stop_price = cast(float, signal.stop_price)
        reason = "STOP" if bid <= stop_price else "TIME" if held >= signal.horizon_sessions else None
        marks.append((signal, held, event, last, reason))
        if reason is not None:
            output.append(PaperBookExit(signal, reason, held, event.event_id, bid))
    book.execute("BEGIN TRANSACTION")
    try:
        for signal, held, _event, last, _ in marks:
            book.execute("UPDATE positions SET last_day=?,last_px=?,held=? WHERE version=? AND mode=? "
                         "AND signal_day=? AND ticker=? AND exit_day IS NULL",
                         [exchange_date, last, held, signal.version, mode, signal.signal_day, signal.ticker])
        book.execute("COMMIT")
    except Exception:
        book.execute("ROLLBACK")
        raise
    return tuple(output)


def record_exit_decision(*, runner: ForwardSessionRunner, session_id: str, exit_signal: PaperBookExit,
                          decided_at: datetime, manifest: dict[str, Any]) -> str:
    """Persist a horizon/stop sell decision; risk approval and execution stay explicit runner steps."""
    signal = exit_signal.signal
    if exit_signal.reason not in {"STOP", "TIME"} or type(exit_signal.completed_sessions_held) is not int \
            or exit_signal.completed_sessions_held < 0:
        raise PaperBookAdapterError("exit reason or completed session age is invalid")
    if exit_signal.reason == "STOP" and exit_signal.bid_price > cast(float, signal.stop_price):
        raise PaperBookAdapterError("stop exit bid is above the frozen stop price")
    if exit_signal.reason == "TIME" and exit_signal.completed_sessions_held < signal.horizon_sessions:
        raise PaperBookAdapterError("time exit precedes the frozen session horizon")
    config = runner.state_store.config(session_id)
    if signal.mode != "live" or config.exchange_date <= signal.signal_day:
        raise PaperBookAdapterError("exit decision requires a live position from an earlier session")
    if signal.instrument_id not in config.instrument_universe:
        raise PaperBookAdapterError("exit instrument is outside the runner's frozen universe")
    try:
        event, _ = runner.source_event(session_id, exit_signal.quote_event_id)
    except ForwardSessionRunnerError as exc:
        raise PaperBookAdapterError("exit decision quote is absent from the runner journal") from exc
    quote = executable_quote(event)
    if event.instrument_id != signal.instrument_id or quote is None or float(quote["bid"]) != exit_signal.bid_price:
        raise PaperBookAdapterError("exit decision quote differs from executable stop/horizon evidence")
    decision_id = f"paper-book-exit:{signal.signal_id}:{session_id}"
    try:
        canonical_bytes(manifest)
    except ManifestError as exc:
        raise PaperBookAdapterError(f"exit decision manifest is invalid: {exc}") from exc
    selections = manifest["selections"]
    if (manifest["decision_id"] != decision_id or manifest["holding_sessions"] != signal.horizon_sessions
            or len(selections) != 1 or selections[0]["lineage"] != signal.lineage
            or selections[0]["symbol"] != signal.instrument_id
            or selections[0]["quantity"] != signal.shares):
        raise PaperBookAdapterError("exit manifest selection or horizon differs from the open position")
    payload = signal.payload() | {"side": "SELL", "exit_reason": exit_signal.reason,
                                  "completed_sessions_held": exit_signal.completed_sessions_held,
                                  "exit_bid_price": exit_signal.bid_price}
    runner.decide(session_id=session_id, strategy_id=signal.version, decision_id=decision_id,
                  outcome="TRADE", input_event_ids=(exit_signal.quote_event_id,),
                  decided_at=decided_at, payload=payload)
    runner.record_decision_manifest(session_id=session_id, decision_id=decision_id, manifest=manifest)
    return decision_id


def reconcile_exit_receipt(*, book: Any, runner: ForwardSessionRunner, session_id: str,
                           exit_signal: PaperBookExit, decision_id: str, order_id: str,
                           receipt: dict[str, Any]) -> str:
    """Mirror a full runner sell to the daily book; retain partial exits for explicit recovery."""
    signal = exit_signal.signal
    records = runner.records(session_id)
    stored = next((item for item in records["execution_receipts"] if item.get("order_id") == order_id), None)
    intent = next((item for item in records["intents"] if item.get("order_id") == order_id), None)
    decision = next((item for item in records["decisions"] if item.get("decision_id") == decision_id), None)
    if stored != receipt or intent is None or intent.get("decision_id") != decision_id or \
            intent.get("side") != "SELL" or decision is None or \
            decision.get("payload", {}).get("signal_id") != signal.signal_id or \
            decision.get("payload", {}).get("exit_reason") != exit_signal.reason:
        raise PaperBookAdapterError("runner sell receipt, intent and book exit do not share one signal identity")
    requested = int(receipt.get("requested_quantity", -1))
    filled = int(receipt.get("filled_quantity", -1))
    if requested != signal.shares or not 0 <= filled <= requested:
        raise PaperBookAdapterError("runner sell quantity differs from the open book position")
    status = str(receipt.get("status", ""))
    if status == "FILLED" and (filled != requested or receipt.get("average_price") is None):
        raise PaperBookAdapterError("FILLED sell receipt has incomplete quantity or price")
    if status == "PARTIAL":
        if not 0 < filled < requested:
            raise PaperBookAdapterError("PARTIAL sell receipt has inconsistent filled quantity")
        state = "PARTIAL_EXIT_REVIEW"
    elif status == "FILLED":
        state = "CLOSED"
    elif status in {"MISSED", "REJECTED"} and filled == 0:
        state = "NO_EXIT_FILL"
    else:
        raise PaperBookAdapterError(f"unsupported exit receipt state {status!r}")
    encoded = json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    charges = _simulated_charges(receipt, order_id)
    config = runner.state_store.config(session_id)
    occurred = datetime.fromisoformat(str(receipt["simulated_at"]).replace("Z", "+00:00"))
    book.execute(BRIDGE_SCHEMA)
    book.execute("ALTER TABLE paper_book_bridge ADD COLUMN IF NOT EXISTS simulated_charges_inr DOUBLE")
    book.execute("BEGIN TRANSACTION")
    try:
        previous = book.execute("SELECT state,receipt_sha256 FROM paper_book_bridge "
                                "WHERE signal_id=? AND session_id=? AND order_id=?",
                                [signal.signal_id, session_id, order_id]).fetchone()
        if previous:
            if previous[1] != digest:
                raise PaperBookAdapterError("runner exit receipt conflicts with prior reconciliation")
            book.execute("COMMIT")
            return str(previous[0])
        if state == "CLOSED":
            entry_cost_row = book.execute(
                "SELECT simulated_charges_inr FROM paper_book_bridge "
                "WHERE signal_id=? AND state='FILLED' ORDER BY updated_at LIMIT 1",
                [signal.signal_id]).fetchone()
            position_row = book.execute(
                "SELECT entry_px,shares FROM positions WHERE version=? AND mode='live' "
                "AND signal_day=? AND ticker=? AND exit_day IS NULL",
                [signal.version, signal.signal_day, signal.ticker]).fetchone()
            if entry_cost_row is None or position_row is None:
                raise PaperBookAdapterError("runner entry fill or open position is missing from bridge")
            entry_charges = entry_cost_row[0]
            entry_notional = float(position_row[0]) * int(position_row[1])
            actual_cost = (float(Decimal(str(entry_charges)) + Decimal(str(charges))) / entry_notional
                           if entry_charges is not None and charges is not None and entry_notional > 0 else None)
            result = book.execute(
                "UPDATE positions SET exit_day=?,exit_px=?,exit_reason=?,last_day=?,last_px=?,held=?,cost=? "
                "WHERE version=? AND mode='live' AND signal_day=? AND ticker=? AND exit_day IS NULL "
                "RETURNING ticker",
                [config.exchange_date, float(receipt["average_price"]), exit_signal.reason,
                 config.exchange_date, float(receipt["average_price"]), exit_signal.completed_sessions_held,
                 actual_cost,
                 signal.version, signal.signal_day, signal.ticker]).fetchone()
            if result is None:
                raise PaperBookAdapterError("open DuckDB position is missing or already closed")
            book.execute("UPDATE paper_book_bridge SET state='CLOSED' WHERE signal_id=? AND state='FILLED'",
                         [signal.signal_id])
        elif state == "PARTIAL_EXIT_REVIEW":
            book.execute("UPDATE paper_book_bridge SET state=? WHERE signal_id=? AND state='FILLED'",
                         [state, signal.signal_id])
        book.execute("INSERT INTO paper_book_bridge (version,mode,signal_day,ticker,signal_id,session_id,order_id,"
                     "state,requested_shares,filled_shares,receipt_sha256,updated_at,simulated_charges_inr) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     [signal.version, signal.mode, signal.signal_day, signal.ticker, signal.signal_id,
                      session_id, order_id, state, requested, filled, digest, occurred, charges])
        book.execute("COMMIT")
        return state
    except Exception:
        book.execute("ROLLBACK")
        raise


__all__ = ["PaperBookAdapterError", "PaperBookSignal", "pending_signals", "record_signal_decision",
           "reconcile_entry_receipt", "PaperBookExit", "due_exits", "record_exit_decision",
           "reconcile_exit_receipt"]
