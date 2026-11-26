"""Append-only normalized market-event archive and feed health diagnostics.

The archive stores observed-event claims plus their provenance; fixture rows remain
fixtures and cannot be mistaken for vendor or exchange observations.
"""
from __future__ import annotations

import json
import hashlib
import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from financial_brain.forward_edge.clock import ClockProvider, SystemClock
from financial_brain.forward_edge.data_distribution import PriceWindow, ProviderWindow
from financial_brain.forward_edge.market_events import MarketEvent


class QuoteArchiveError(ValueError):
    """Archive rejected a duplicate, conflicting or invalid event."""


@dataclass(frozen=True)
class FeedHealth:
    received: int
    sequence_gaps: int
    late_events: int
    max_transport_lag_ms: float
    last_received_at: datetime | None


@dataclass(frozen=True)
class CoverageReport:
    expected_sessions: int
    covered_sessions: int
    expected_names: int
    covered_names: int
    requested_intent_notional: float
    covered_intent_notional: float
    quote_coverage_fraction: float
    depth_coverage_fraction: float
    source_classes: tuple[str, ...]
    missing_keys: tuple[str, ...]

    @property
    def claim_scope(self) -> str:
        return "LIVE_OBSERVED" if self.source_classes == ("live",) else "FIXTURE_OR_MIXED"


class QuoteArchive:
    def __init__(self, path: Path, *, clock: ClockProvider | None = None) -> None:
        self.path = Path(path)
        self.clock = clock or SystemClock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS market_events (
                event_id TEXT PRIMARY KEY, kind TEXT NOT NULL, venue TEXT NOT NULL,
                instrument_id TEXT NOT NULL, event_time TEXT NOT NULL, received_at TEXT NOT NULL,
                available_at TEXT NOT NULL, sequence INTEGER, source TEXT NOT NULL,
                evidence_id TEXT, event_sha256 TEXT NOT NULL, event_json TEXT NOT NULL,
                ingested_at TEXT
            )
        """)
        columns = {row[1] for row in self._con.execute("PRAGMA table_info(market_events)")}
        if "ingested_at" not in columns:
            # Older archives cannot recover their original write time; NULL preserves that fact.
            self._con.execute("ALTER TABLE market_events ADD COLUMN ingested_at TEXT")
        self._con.execute("""CREATE TABLE IF NOT EXISTS market_event_fingerprints (
            fingerprint_sha256 TEXT PRIMARY KEY, event_id TEXT NOT NULL UNIQUE)""")
        self._con.execute("""CREATE TABLE IF NOT EXISTS market_event_revisions (
            event_id TEXT PRIMARY KEY, semantic_sha256 TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
            revision INTEGER NOT NULL, supersedes_event_id TEXT)""")
        self._backfill_event_identity()
        self._con.commit()

    @staticmethod
    def _event_identity(event: MarketEvent) -> tuple[str, str, str]:
        """Return semantic key, payload hash and exact-observation fingerprint."""
        payload_raw = json.dumps(event.payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        payload_hash = hashlib.sha256(payload_raw.encode("utf-8")).hexdigest()
        source_clock = event.exchange_at or event.source_at or event.event_time
        semantic = {"venue": event.venue, "instrument_id": event.instrument_id,
                    "source": event.source, "kind": event.kind.value,
                    "source_clock": source_clock.isoformat()}
        semantic_raw = json.dumps(semantic, sort_keys=True, separators=(",", ":"), allow_nan=False)
        semantic_hash = hashlib.sha256(semantic_raw.encode("utf-8")).hexdigest()
        fingerprint_raw = f"{semantic_hash}:{payload_hash}"
        fingerprint = hashlib.sha256(fingerprint_raw.encode("ascii")).hexdigest()
        return semantic_hash, payload_hash, fingerprint

    def _backfill_event_identity(self) -> None:
        """Add correction and duplicate lineage to legacy rows without deleting history."""
        rows = self._con.execute("SELECT event_id, event_json FROM market_events "
                                 "ORDER BY event_time, received_at, event_id").fetchall()
        latest: dict[str, tuple[int, str, str]] = {}
        for event_id, body in rows:
            event = MarketEvent.from_dict(json.loads(body))
            semantic_hash, payload_hash, fingerprint = self._event_identity(event)
            prior = latest.get(semantic_hash)
            revision = 1 if prior is None else prior[0] + 1
            supersedes = prior[1] if prior else None
            if self._con.execute("SELECT 1 FROM market_event_revisions WHERE event_id=?", [event_id]).fetchone():
                existing = self._con.execute(
                    "SELECT revision, supersedes_event_id FROM market_event_revisions WHERE event_id=?",
                    [event_id]).fetchone()
                revision, supersedes = int(existing[0]), existing[1]
            else:
                self._con.execute("INSERT INTO market_event_revisions VALUES (?, ?, ?, ?, ?)",
                                  [event_id, semantic_hash, payload_hash, revision, supersedes])
            self._con.execute("INSERT OR IGNORE INTO market_event_fingerprints VALUES (?, ?)",
                              [fingerprint, event_id])
            latest[semantic_hash] = (revision, event_id, payload_hash)

    def close(self) -> None:
        self._con.close()

    def append(self, event: MarketEvent, *, ingested_at: datetime | None = None) -> bool:
        ingested_at = ingested_at or self.clock.now()
        if ingested_at.tzinfo is None or ingested_at.utcoffset() is None:
            raise QuoteArchiveError("ingest clock must be timezone aware")
        ingested_at = ingested_at.astimezone(timezone.utc)
        if ingested_at < event.available_at:
            raise QuoteArchiveError("ingest time cannot precede event availability")
        try:
            MarketEvent.from_dict(event.as_dict())
        except (TypeError, ValueError) as exc:
            raise QuoteArchiveError("event payload changed or became invalid before archive append") from exc
        body = event.as_dict()
        digest = event.sha256()
        semantic_hash, payload_hash, fingerprint = self._event_identity(event)
        self._con.execute("BEGIN IMMEDIATE")
        try:
            previous = self._con.execute("SELECT event_sha256 FROM market_events WHERE event_id=?",
                                         [event.event_id]).fetchone()
            if previous:
                if previous[0] != digest:
                    raise QuoteArchiveError("event_id reused with different content")
                self._con.commit()
                return False
            duplicate = self._con.execute(
                "SELECT event_id FROM market_event_fingerprints WHERE fingerprint_sha256=?", [fingerprint]
            ).fetchone()
            if duplicate:
                self._con.commit()
                return False
            prior = self._con.execute(
                "SELECT event_id, revision FROM market_event_revisions WHERE semantic_sha256=? "
                "ORDER BY revision DESC LIMIT 1", [semantic_hash]).fetchone()
            revision = int(prior[1]) + 1 if prior else 1
            supersedes = prior[0] if prior else None
            self._con.execute("""INSERT INTO market_events (
                              event_id, kind, venue, instrument_id, event_time, received_at,
                              available_at, sequence, source, evidence_id, event_sha256, event_json,
                              ingested_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                              [event.event_id, event.kind.value, event.venue, event.instrument_id,
                               event.event_time.isoformat(), event.received_at.isoformat(),
                               event.available_at.isoformat(), event.sequence, event.source,
                               event.evidence_id, digest,
                               json.dumps(body, sort_keys=True, separators=(",", ":")),
                               ingested_at.isoformat()])
            self._con.execute("INSERT INTO market_event_fingerprints VALUES (?, ?)",
                              [fingerprint, event.event_id])
            self._con.execute("INSERT INTO market_event_revisions VALUES (?, ?, ?, ?, ?)",
                              [event.event_id, semantic_hash, payload_hash, revision, supersedes])
            self._con.commit()
            return True
        except Exception:
            self._con.rollback()
            raise

    def revision_lineage(self, event_id: str) -> dict[str, object]:
        """Return immutable correction lineage for an event; late distinct clocks remain separate."""
        row = self._con.execute("""SELECT semantic_sha256, payload_sha256, revision, supersedes_event_id
            FROM market_event_revisions WHERE event_id=?""", [event_id]).fetchone()
        if row is None:
            raise KeyError(event_id)
        return {"event_id": event_id, "semantic_sha256": row[0], "payload_sha256": row[1],
                "revision": int(row[2]), "supersedes_event_id": row[3]}

    def ingest_time(self, event_id: str) -> datetime | None:
        """Return the original archive append clock; legacy rows remain explicitly unknown."""
        row = self._con.execute("SELECT ingested_at FROM market_events WHERE event_id=?", [event_id]).fetchone()
        if row is None:
            raise KeyError(event_id)
        return datetime.fromisoformat(row[0]) if row[0] is not None else None

    def get(self, event_id: str) -> MarketEvent:
        row = self._con.execute("SELECT event_json FROM market_events WHERE event_id=?", [event_id]).fetchone()
        if row is None:
            raise KeyError(event_id)
        return MarketEvent.from_dict(json.loads(row[0]))

    def events_for_session(self, session_date: date, *, venue: str | None = None,
                           source: str | None = None) -> list[MarketEvent]:
        """Read normalized events whose exchange timestamp belongs to the India session date."""
        ist = ZoneInfo("Asia/Kolkata")
        start = datetime.combine(session_date, time.min, ist).astimezone(timezone.utc).isoformat()
        end = (datetime.combine(session_date, time.min, ist) + timedelta(days=1)).astimezone(timezone.utc).isoformat()
        clauses = ["event_time>=?", "event_time<?"]
        params: list[str] = [start, end]
        if venue is not None:
            clauses.append("venue=?")
            params.append(venue.upper())
        if source is not None:
            clauses.append("source=?")
            params.append(source)
        rows = self._con.execute("SELECT event_json FROM market_events WHERE " + " AND ".join(clauses) +
                                 " ORDER BY available_at, event_id", params).fetchall()
        return [MarketEvent.from_dict(json.loads(row[0])) for row in rows]

    def snapshot(self, *, decision_id: str, venue: str, instrument_id: str, decision_time: datetime) -> dict[str, object]:
        """Freeze exactly those archived events available no later than the decision clock."""
        if not decision_id.strip() or decision_time.tzinfo is None or decision_time.utcoffset() is None:
            raise ValueError("decision ID and timezone-aware decision_time are required")
        cutoff = decision_time.astimezone(timezone.utc).isoformat()
        rows = self._con.execute("""
            SELECT event_id, event_sha256, available_at FROM market_events
            WHERE venue=? AND instrument_id=? AND available_at<=?
            ORDER BY available_at, event_id
        """, [venue, instrument_id, cutoff]).fetchall()
        members = [{"event_id": event_id, "sha256": digest, "available_at": available}
                   for event_id, digest, available in rows]
        if not members:
            raise QuoteArchiveError("no archived events were available at decision time")
        body: dict[str, object] = {"schema": "fb.market-decision-snapshot.v1", "decision_id": decision_id,
                                  "venue": venue, "instrument_id": instrument_id,
                                  "decision_time": cutoff, "events": members}
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
        body["snapshot_sha256"] = __import__("hashlib").sha256(canonical.encode("utf-8")).hexdigest()
        return body

    def health(self, *, venue: str, instrument_id: str, as_of: datetime | None = None) -> FeedHealth:
        # `as_of` is retained for a stable report contract; rows are not filtered
        # because the archive health summary measures observed history, not a cutoff.
        if as_of is not None and (as_of.tzinfo is None or as_of.utcoffset() is None):
            raise ValueError("as_of must be timezone aware")
        rows = self._con.execute("""
            SELECT event_time, received_at, sequence FROM market_events
            WHERE venue=? AND instrument_id=? ORDER BY received_at, event_id
        """, [venue, instrument_id]).fetchall()
        sequences = [int(seq) for _, _, seq in rows if seq is not None]
        gaps = sum(max(0, later - earlier - 1) for earlier, later in zip(sequences, sequences[1:]))
        lags = [(datetime.fromisoformat(received) - datetime.fromisoformat(event)).total_seconds() * 1000
                for event, received, _ in rows]
        late = sum(datetime.fromisoformat(event) > datetime.fromisoformat(received) for event, received, _ in rows)
        last = max((datetime.fromisoformat(r[1]) for r in rows), default=None)
        if last is not None:
            last = last.astimezone(timezone.utc)
        return FeedHealth(len(rows), gaps, late, max(lags, default=0.0), last)

    def provider_window(self, *, start: datetime, end: datetime, as_of: datetime,
                        expected_quote_observations: int,
                        max_transport_lag: timedelta) -> ProviderWindow:
        """Build a source-quality window from archived events, preserving empty outages.

        Observation membership follows the source event clock; ``as_of`` limits the report
        to data that had become available by that time. The expected quote denominator is
        explicit because an empty archive cannot infer how many packets should have arrived.
        """
        if any(value.tzinfo is None or value.utcoffset() is None for value in (start, end, as_of)):
            raise QuoteArchiveError("provider-window clocks must be timezone aware")
        if end <= start or as_of < end:
            raise QuoteArchiveError("provider-window interval and report cutoff are invalid")
        if type(expected_quote_observations) is not int or expected_quote_observations < 1:
            raise QuoteArchiveError("expected quote observation count must be positive")
        if not isinstance(max_transport_lag, timedelta) or max_transport_lag < timedelta(0):
            raise QuoteArchiveError("maximum transport lag must be non-negative")
        lower, upper, cutoff = (value.astimezone(timezone.utc).isoformat()
                                for value in (start, end, as_of))
        rows = self._con.execute("""SELECT event_id, kind, event_time, received_at,
            available_at, source, sequence, event_json FROM market_events
            WHERE event_time>=? AND event_time<? AND available_at<=?
            ORDER BY received_at, event_id""", [lower, upper, cutoff]).fetchall()
        sources: dict[str, int] = {}
        quote_rows: list[tuple[datetime, datetime, dict]] = []
        schema_keys: dict[str, set[str]] = {}
        out_of_order = 0
        previous_event: datetime | None = None
        previous_sequence: dict[str, int] = {}
        for row in rows:
            _, kind, event_raw, received_raw, _, source, sequence, body_raw = row
            event_at = datetime.fromisoformat(event_raw).astimezone(timezone.utc)
            received_at = datetime.fromisoformat(received_raw).astimezone(timezone.utc)
            body = json.loads(body_raw)
            payload = body.get("payload", {})
            if not isinstance(payload, dict):
                payload = {}
            sources[source] = sources.get(source, 0) + 1
            schema_keys.setdefault(kind, set()).update(body.keys() | payload.keys())
            if kind == "quote":
                quote_rows.append((event_at, received_at, payload))
            out_of_order_row = previous_event is not None and event_at < previous_event
            if sequence is not None:
                prior_sequence = previous_sequence.get(source)
                if prior_sequence is not None and int(sequence) <= prior_sequence:
                    out_of_order_row = True
                previous_sequence[source] = int(sequence)
            out_of_order += int(out_of_order_row)
            previous_event = event_at

        valid_quotes = 0
        stale_quotes = 0
        for event_at, received_at, payload in quote_rows:
            bid, ask = payload.get("bid"), payload.get("ask")
            try:
                valid = (isinstance(bid, (int, float)) and not isinstance(bid, bool)
                         and isinstance(ask, (int, float)) and not isinstance(ask, bool)
                         and 0 < float(bid) <= float(ask) and math.isfinite(float(ask)))
            except (TypeError, ValueError, OverflowError):
                valid = False
            valid_quotes += int(valid)
            lag = received_at - event_at
            stale_quotes += int(lag < timedelta(0) or lag > max_transport_lag)

        observation_count = len(rows)
        source_mix = tuple(sorted((name, count / observation_count)
                                  for name, count in sources.items())) if observation_count else ()
        schema = {
            "archive_columns": [row[1] for row in self._con.execute("PRAGMA table_info(market_events)")],
            "event_fields_by_kind": {kind: sorted(keys) for kind, keys in sorted(schema_keys.items())},
        }
        schema_sha = hashlib.sha256(json.dumps(
            schema, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()
        return ProviderWindow(
            start.astimezone(timezone.utc), end.astimezone(timezone.utc), observation_count,
            min(1.0, len(quote_rows) / expected_quote_observations),
            stale_quotes / len(quote_rows) if quote_rows else 1.0,
            out_of_order / max(1, observation_count - 1),
            1.0 - valid_quotes / len(quote_rows) if quote_rows else 1.0,
            source_mix, schema_sha,
        )

    def price_window(self, *, start: datetime, end: datetime, as_of: datetime,
                     venue: str, instrument_id: str) -> PriceWindow:
        """Aggregate observed midpoint returns/volatility using only data available by ``as_of``.

        Quotes with malformed or crossed prices are excluded. The return series is based
        on consecutive valid midpoints in receipt order; no missing interval is interpolated.
        At least two distinct prices are required by PriceWindow.
        """
        if any(value.tzinfo is None or value.utcoffset() is None for value in (start, end, as_of)):
            raise QuoteArchiveError("price-window clocks must be timezone aware")
        if end <= start or as_of < end or not venue.strip() or not instrument_id.strip():
            raise QuoteArchiveError("price-window bounds, cutoff and instrument are invalid")
        rows = self._con.execute("""SELECT event_json FROM market_events
            WHERE venue=? AND instrument_id=? AND kind='quote'
              AND event_time>=? AND event_time<? AND available_at<=?
            ORDER BY received_at, event_id""",
            [venue.upper(), instrument_id, start.astimezone(timezone.utc).isoformat(),
             end.astimezone(timezone.utc).isoformat(), as_of.astimezone(timezone.utc).isoformat()]).fetchall()
        mids: list[float] = []
        for (body_raw,) in rows:
            payload = json.loads(body_raw).get("payload", {})
            bid, ask = payload.get("bid"), payload.get("ask")
            if (isinstance(bid, (int, float)) and not isinstance(bid, bool)
                    and isinstance(ask, (int, float)) and not isinstance(ask, bool)
                    and math.isfinite(float(bid)) and math.isfinite(float(ask))
                    and 0 < float(bid) <= float(ask)):
                mids.append((float(bid) + float(ask)) / 2)
        if len(mids) < 2:
            raise QuoteArchiveError("price window needs at least two valid available quotes")
        returns = [(right / left) - 1.0 for left, right in zip(mids, mids[1:])]
        mean_return = sum(returns) / len(returns)
        volatility = math.sqrt(sum((value - mean_return) ** 2 for value in returns) / len(returns))
        return PriceWindow(start.astimezone(timezone.utc), end.astimezone(timezone.utc), len(mids),
                           volatility, (mids[-1] / mids[0]) - 1.0)

    def replay_markouts(self, *, venue: str, instrument_id: str, side: str,
                        execution_price: float, execution_at: datetime, as_of: datetime,
                        authorized_use: bool, horizons: tuple[timedelta, ...] =
                        (timedelta(seconds=5), timedelta(seconds=30), timedelta(minutes=1),
                         timedelta(minutes=15))) -> dict[str, object]:
        """Measure side-adjusted quote markout at exact standard horizons when lawful.

        ``authorized_use`` must be backed by the caller's reviewed source/use permission;
        false or unknown use fails closed. The archive itself never establishes legal rights.
        A quote after the target horizon is not substituted for a missing exact/late horizon.
        Positive bps means favorable movement for the position side.
        """
        if not authorized_use:
            raise QuoteArchiveError("markout replay requires affirmative reviewed data-use authorization")
        if (side not in {"BUY", "SELL"} or not math.isfinite(execution_price) or execution_price <= 0
                or not venue.strip() or not instrument_id.strip() or
                any(value.tzinfo is None or value.utcoffset() is None for value in (execution_at, as_of))):
            raise QuoteArchiveError("markout requires side, positive fill price, identity and aware clocks")
        if as_of < execution_at or not horizons or any(h <= timedelta(0) for h in horizons):
            raise QuoteArchiveError("markout horizons and report cutoff must follow execution")
        if len(set(horizons)) != len(horizons):
            raise QuoteArchiveError("markout horizons must be unique")
        rows = self._con.execute("""SELECT event_json, event_sha256 FROM market_events
            WHERE venue=? AND instrument_id=? AND kind='quote' AND available_at<=?
              AND event_time>=? ORDER BY event_time, available_at, event_id""",
            [venue.upper(), instrument_id, as_of.astimezone(timezone.utc).isoformat(),
             execution_at.astimezone(timezone.utc).isoformat()]).fetchall()
        events = [(MarketEvent.from_dict(json.loads(raw)), digest) for raw, digest in rows]
        reports = []
        for horizon in sorted(horizons):
            target = execution_at.astimezone(timezone.utc) + horizon
            match = next(((event, digest) for event, digest in events
                          if event.event_time.astimezone(timezone.utc) >= target), None)
            if match is None:
                reports.append({"horizon_seconds": horizon.total_seconds(), "status": "UNAVAILABLE",
                                "reason": "NO_QUOTE_AT_OR_AFTER_HORIZON_BY_CUTOFF",
                                "markout_bps": None, "event_sha256": None})
                continue
            event, digest = match
            payload = event.payload
            bid, ask = payload.get("bid"), payload.get("ask")
            if (not isinstance(bid, (int, float)) or not isinstance(ask, (int, float))
                    or isinstance(bid, bool) or isinstance(ask, bool)
                    or not math.isfinite(float(bid)) or not math.isfinite(float(ask))
                    or not 0 < float(bid) <= float(ask)):
                reports.append({"horizon_seconds": horizon.total_seconds(), "status": "UNAVAILABLE",
                                "reason": "INVALID_TWO_SIDED_QUOTE", "markout_bps": None,
                                "event_sha256": digest})
                continue
            mark = (float(bid) + float(ask)) / 2
            markout = ((mark - execution_price) if side == "BUY" else (execution_price - mark)) \
                / execution_price * 10_000
            reports.append({"horizon_seconds": horizon.total_seconds(), "status": "MEASURED",
                            "event_id": event.event_id, "event_sha256": digest,
                            "event_time": event.event_time.astimezone(timezone.utc).isoformat(),
                            "available_at": event.available_at.astimezone(timezone.utc).isoformat(),
                            "source": event.source, "mark_price": mark, "markout_bps": markout})
        body = {"schema": "fb.replay_markout.v1", "venue": venue.upper(),
                "instrument_id": instrument_id, "side": side, "execution_price": execution_price,
                "execution_at": execution_at.astimezone(timezone.utc).isoformat(),
                "as_of": as_of.astimezone(timezone.utc).isoformat(),
                "evidence_tier": "HISTORICAL_REPLAY", "horizons": reports}
        body["report_sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                                allow_nan=False).encode()).hexdigest()
        return body

    def count(self) -> int:
        return int(self._con.execute("SELECT count(*) FROM market_events").fetchone()[0])

    def rows(self) -> list[dict[str, object]]:
        names = [x[1] for x in self._con.execute("PRAGMA table_info(market_events)").fetchall()]
        return [dict(zip(names, row, strict=True)) for row in self._con.execute(
            "SELECT * FROM market_events ORDER BY received_at, event_id").fetchall()]

    def instrument_day_quality_report(
        self,
        *,
        venue: str,
        instrument_id: str,
        session_day: date,
        as_of: datetime,
        expected_event_count: int | None,
        max_lateness_ms: int = 1_000,
        max_quote_age_ms: int = 5_000,
        strike_selection_evidence: Sequence[dict[str, Any]] = (),
        require_strike_selection: bool = False,
    ) -> dict[str, Any]:
        """Build a point-in-time data-quality report over this archive's sourced events."""
        from financial_brain.forward_edge.market_quality import build_instrument_day_quality_report

        events = self.events_for_session(session_day, venue=venue)
        return build_instrument_day_quality_report(
            events, venue=venue, instrument_id=instrument_id, session_day=session_day, as_of=as_of,
            expected_event_count=expected_event_count, max_lateness_ms=max_lateness_ms,
            max_quote_age_ms=max_quote_age_ms, strike_selection_evidence=strike_selection_evidence,
            require_strike_selection=require_strike_selection,
        )

    def coverage(self, *, expected_sessions: set[str],
                 intents: dict[tuple[str, str], float], as_of: datetime) -> CoverageReport:
        """Measure session/name/notional quote and depth coverage against explicit denominators.

        `intents` keys are `(session_id, instrument_id)` and values are requested INR notional.
        Kite Connect and exchange source labels are treated as observed-feed classes; this report
        does not authenticate them, so independent evidence is still required for formal claims.
        """
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone aware")
        if not expected_sessions or any(not k[0] or not k[1] for k in intents):
            raise ValueError("coverage needs expected sessions and valid intent keys")
        if any(not isinstance(value, (int, float)) or value < 0 for value in intents.values()):
            raise ValueError("intent notionals must be finite non-negative numbers")
        by_session_names: dict[str, set[str]] = {s: set() for s in expected_sessions}
        covered_quotes: set[tuple[str, str]] = set()
        covered_depth: set[tuple[str, str]] = set()
        sources: set[str] = set()
        missing: list[str] = []
        cutoff = as_of.astimezone(timezone.utc)
        for session_id, instrument_id in intents:
            if session_id not in expected_sessions:
                raise ValueError(f"intent session {session_id} is not in expected_sessions")
            by_session_names[session_id].add(instrument_id)
            quote = self._con.execute("""
                SELECT kind, event_time, received_at, source FROM market_events
                WHERE instrument_id = ? AND available_at <= ? AND kind IN ('quote','book_depth')
                ORDER BY available_at DESC, event_id LIMIT 10
            """, [instrument_id, cutoff.isoformat()]).fetchall()
            quote_ok = any(kind == "quote" for kind, *_ in quote)
            depth_ok = any(kind == "book_depth" for kind, *_ in quote)
            key = (session_id, instrument_id)
            if quote_ok:
                covered_quotes.add(key)
            if depth_ok:
                covered_depth.add(key)
            if not quote_ok:
                missing.append(f"{session_id}:{instrument_id}:QUOTE")
            if not depth_ok:
                missing.append(f"{session_id}:{instrument_id}:DEPTH")
            sources.update("live" if source.lower() in {
                "kite", "kite_connect_read_only", "exchange", "zerodha_kite"
            } else "fixture"
                           for _, _, _, source in quote)
        expected_name_keys = [(s, name) for s in expected_sessions
                              for session, name in intents if session == s]
        expected_notional = sum(float(v) for v in intents.values())
        quote_notional = sum(float(intents[k]) for k in covered_quotes)
        depth_notional = sum(float(intents[k]) for k in covered_depth)
        quote_fraction = quote_notional / expected_notional if expected_notional else 0.0
        depth_fraction = depth_notional / expected_notional if expected_notional else 0.0
        covered_sessions = {session for session, _ in covered_quotes}
        covered_names = {instrument for _, instrument in covered_quotes}
        name_count = len({instrument for _, instrument in expected_name_keys})
        return CoverageReport(len(expected_sessions), len(covered_sessions), name_count, len(covered_names),
                              expected_notional, quote_notional, quote_fraction, depth_fraction,
                              tuple(sorted(sources)), tuple(sorted(missing)))
