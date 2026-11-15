"""Immutable, as-of signal decision traces for the forward paper cockpit (FE-0116)."""
from __future__ import annotations

import hashlib
import html
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class SignalTraceError(ValueError):
    """Signal trace is incomplete, look-ahead contaminated or conflicts with prior evidence."""


@dataclass(frozen=True)
class TraceEvidence:
    source_id: str
    sha256: str
    available_at: datetime

    def __post_init__(self) -> None:
        if not self.source_id.strip() or len(self.sha256) != 64 or any(
                char not in "0123456789abcdef" for char in self.sha256):
            raise SignalTraceError("evidence needs a source ID and lowercase SHA-256")
        if self.available_at.tzinfo is None or self.available_at.utcoffset() is None:
            raise SignalTraceError("evidence availability time must be timezone aware")


@dataclass(frozen=True)
class SignalTrace:
    signal_id: str
    strategy_id: str
    strategy_version: str
    symbol: str
    decision: str
    decision_at: datetime
    evidence: tuple[TraceEvidence, ...]
    bid: float | None
    ask: float | None
    volume: int | None
    lower_price_limit: float | None
    upper_price_limit: float | None
    expires_at: datetime | None
    side: str | None
    quantity: int | None
    order_type: str | None
    limit_price: float | None
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not all(value.strip() for value in
                   (self.signal_id, self.strategy_id, self.strategy_version, self.symbol)):
            raise SignalTraceError("signal and strategy identity are required")
        if self.decision not in {"TRADE", "NO_TRADE", "WATCH"}:
            raise SignalTraceError("decision must be TRADE, NO_TRADE or WATCH")
        if self.decision_at.tzinfo is None or self.decision_at.utcoffset() is None:
            raise SignalTraceError("decision time must be timezone aware")
        if not self.evidence:
            raise SignalTraceError("at least one evidence snapshot is required")
        if any(item.available_at > self.decision_at for item in self.evidence):
            raise SignalTraceError("future evidence is not permitted in a signal trace")
        for label, value in (("bid", self.bid), ("ask", self.ask),
                             ("lower price limit", self.lower_price_limit),
                             ("upper price limit", self.upper_price_limit),
                             ("limit price", self.limit_price)):
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise SignalTraceError(f"{label} must be finite and positive")
        if (self.bid is None) != (self.ask is None) or (
                self.bid is not None and self.ask is not None and self.bid > self.ask):
            raise SignalTraceError("bid and ask must be supplied together and ordered")
        if self.volume is not None and (type(self.volume) is not int or self.volume < 0):
            raise SignalTraceError("volume must be a non-negative integer or missing")
        if (self.lower_price_limit is not None and self.upper_price_limit is not None
                and self.lower_price_limit > self.upper_price_limit):
            raise SignalTraceError("lower price limit cannot exceed upper limit")
        if self.expires_at is not None:
            if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
                raise SignalTraceError("expiry must be timezone aware")
        intended = (self.side, self.quantity, self.order_type)
        if self.decision == "TRADE":
            if any(value is None for value in intended):
                raise SignalTraceError("trade decision requires an intended order")
            if self.side not in {"BUY", "SELL"} or self.quantity is None or self.quantity < 1:
                raise SignalTraceError("order side and quantity are invalid")
            if self.order_type not in {"MARKET", "LIMIT", "STOP"}:
                raise SignalTraceError("unsupported intended order type")
            if self.order_type == "LIMIT" and self.limit_price is None:
                raise SignalTraceError("limit order needs a limit price")
        elif any(value is not None for value in intended) or self.limit_price is not None:
            raise SignalTraceError("NO_TRADE and WATCH cannot contain order intent fields")
        if self.decision == "NO_TRADE" and not self.reason_codes:
            raise SignalTraceError("NO_TRADE requires reason codes")
        if any(not reason.strip() for reason in self.reason_codes):
            raise SignalTraceError("reason codes cannot be blank")

    @property
    def spread_bps(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        mid = (self.bid + self.ask) / 2
        return (self.ask - self.bid) / mid * 10_000

    def body(self) -> dict[str, object]:
        return {
            "signal_id": self.signal_id, "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version, "symbol": self.symbol,
            "decision": self.decision,
            "decision_at": self.decision_at.astimezone(timezone.utc).isoformat(),
            "evidence": [{"source_id": item.source_id, "sha256": item.sha256,
                          "available_at": item.available_at.astimezone(timezone.utc).isoformat()}
                         for item in self.evidence],
            "bid": self.bid, "ask": self.ask, "spread_bps": self.spread_bps,
            "volume": self.volume, "lower_price_limit": self.lower_price_limit,
            "upper_price_limit": self.upper_price_limit,
            "expires_at": self.expires_at.astimezone(timezone.utc).isoformat()
            if self.expires_at else None,
            "side": self.side, "quantity": self.quantity, "order_type": self.order_type,
            "limit_price": self.limit_price, "reason_codes": sorted(set(self.reason_codes)),
        }

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.body(), sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()


class SignalTraceJournal:
    """Append-only SQLite journal for immutable signal snapshots."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("CREATE TABLE IF NOT EXISTS signal_traces (signal_id TEXT PRIMARY KEY, digest TEXT NOT NULL, body TEXT NOT NULL)")
        self._con.execute("CREATE TRIGGER IF NOT EXISTS signal_traces_no_update BEFORE UPDATE ON signal_traces BEGIN SELECT RAISE(ABORT,'signal traces are immutable'); END")
        self._con.execute("CREATE TRIGGER IF NOT EXISTS signal_traces_no_delete BEFORE DELETE ON signal_traces BEGIN SELECT RAISE(ABORT,'signal traces are immutable'); END")
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def append(self, trace: SignalTrace) -> bool:
        body = json.dumps(trace.body(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        prior = self._con.execute("SELECT digest,body FROM signal_traces WHERE signal_id=?",
                                  [trace.signal_id]).fetchone()
        if prior:
            if prior != (trace.digest(), body):
                raise SignalTraceError("signal ID reused with changed evidence")
            return False
        self._con.execute("INSERT INTO signal_traces VALUES (?,?,?)",
                          [trace.signal_id, trace.digest(), body])
        self._con.commit()
        return True

    def get(self, signal_id: str) -> dict[str, object] | None:
        row = self._con.execute("SELECT body FROM signal_traces WHERE signal_id=?",
                                [signal_id]).fetchone()
        return json.loads(row[0]) if row else None


def render_signal_trace(trace: SignalTrace, output: Path) -> Path:
    """Render a trace detail page; all text from signal or source is escaped."""
    body = trace.body()
    evidence = "".join("<li>" + html.escape(item.source_id) + " · " + html.escape(item.sha256)
                       + " · available " + html.escape(item.available_at.isoformat()) + "</li>"
                       for item in trace.evidence)
    facts = [("decision", trace.decision), ("symbol", trace.symbol),
             ("side", trace.side or "None"), ("quantity", str(trace.quantity or "Unknown")),
             ("order type", trace.order_type or "None"),
             ("bid / ask", f"{trace.bid} / {trace.ask}" if trace.bid is not None else "Unknown"),
             ("spread bps", str(trace.spread_bps) if trace.spread_bps is not None else "Unknown"),
             ("volume", str(trace.volume) if trace.volume is not None else "Unknown"),
             ("price limits", f"{trace.lower_price_limit} – {trace.upper_price_limit}"),
             ("expiry", trace.expires_at.isoformat() if trace.expires_at else "None"),
             ("limit price", str(trace.limit_price) if trace.limit_price is not None else "None"),
             ("reasons", ", ".join(trace.reason_codes) or "None")]
    rows = "".join("<tr><th>" + html.escape(label) + "</th><td>" + html.escape(value)
                    + "</td></tr>" for label, value in facts)
    page = ("<!doctype html><html lang='en'><meta charset='utf-8'><title>Signal evidence trace</title>"
            "<style>body{font:15px system-ui;max-width:900px;margin:2rem auto;padding:0 1rem}"
            "table{border-collapse:collapse}th,td{padding:.45rem;border-bottom:1px solid #ddd;text-align:left}"
            "code{overflow-wrap:anywhere}</style><main><h1>Signal evidence trace</h1><p>Signal: <b>"
            + html.escape(trace.signal_id) + "</b> · Strategy: " + html.escape(trace.strategy_id)
            + "@" + html.escape(trace.strategy_version) + " · Decision at "
            + html.escape(trace.decision_at.isoformat()) + "</p><p>SHA-256: <code>" + trace.digest()
            + "</code></p><table>" + rows + "</table><h2>As-of evidence</h2><ul>" + evidence
            + "</ul><p>Canonical fields: <code>" + html.escape(json.dumps(body, sort_keys=True))
            + "</code></p></main></html>")
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(page, encoding="utf-8")
    return destination
