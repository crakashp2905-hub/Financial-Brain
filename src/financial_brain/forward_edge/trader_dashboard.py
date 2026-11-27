"""Five-desk evidence-labeled paper trader dashboard snapshot and HTML renderer."""
from __future__ import annotations

import hashlib
import html
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class TraderDashboardError(ValueError):
    """Dashboard snapshots are incomplete, stale, or internally inconsistent."""


DESKS = frozenset({"SWING", "INTRADAY", "OPTIONS", "ETF", "MUTUAL_FUND"})
SOURCE_MODES = frozenset({"OBSERVED_MARKET_DATA", "FIXTURE", "HISTORICAL", "PAPER", "HYPOTHETICAL"})
ACTION_STATES = frozenset({"BUY", "SELL_TO_CLOSE", "BUY_TO_COVER", "HOLD", "WATCH", "NO_TRADE", "REJECTED", "UNRESOLVED"})
_SHA = re.compile(r"^[0-9a-f]{64}$")


def _trace_digest(event: "TraceEvent") -> str:
    body = {"event_id": event.event_id, "kind": event.kind,
            "occurred_at": event.occurred_at.astimezone(timezone.utc).isoformat(),
            "payload_sha256": event.payload_sha256,
            "previous_event_sha256": event.previous_event_sha256}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class DirectionForecast:
    """Synchronized directional forecast; does not itself represent an order."""
    as_of: datetime
    p_up: float
    p_down: float
    p_flat: float
    expected_low: float
    expected_high: float
    model_validity: str
    calibration_status: str

    def validate(self) -> None:
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise TraderDashboardError("forecast time must be timezone-aware")
        values = (self.p_up, self.p_down, self.p_flat, self.expected_low, self.expected_high)
        if any(not math.isfinite(value) for value in values):
            raise TraderDashboardError("forecast probabilities and range must be finite")
        if any(value < 0 or value > 1 for value in (self.p_up, self.p_down, self.p_flat)):
            raise TraderDashboardError("forecast probabilities must be in [0, 1]")
        if not math.isclose(self.p_up + self.p_down + self.p_flat, 1.0, abs_tol=1e-6):
            raise TraderDashboardError("UP/DOWN/FLAT probabilities must sum to one")
        if self.expected_low <= 0 or self.expected_high < self.expected_low:
            raise TraderDashboardError("expected range must be positive and ordered")
        if not self.model_validity.strip() or not self.calibration_status.strip():
            raise TraderDashboardError("model validity and calibration status are required")


@dataclass(frozen=True)
class DashboardDecision:
    """A forecast-independent, explicit recommendation/position state."""
    decision_id: str
    instrument: str
    action: str
    decision_at: datetime
    reason: str
    expected_cost_inr: float | None = None
    fee_uncertainty_inr: float | None = None
    stop_distance: float | None = None
    quantity: float | None = None
    blocked_reason: str | None = None
    option_expiry: str | None = None
    option_strike: float | None = None
    option_right: str | None = None
    option_lot_size: int | None = None
    bid: float | None = None
    ask: float | None = None
    quote_source: str | None = None
    quote_at: datetime | None = None
    maximum_quote_age_seconds: float | None = None
    sl_policy_trigger: float | None = None
    tp_policy_trigger: float | None = None
    actual_exit_price: float | None = None
    actual_exit_at: datetime | None = None

    def validate(self) -> None:
        if not self.decision_id.strip() or not self.instrument.strip() or not self.reason.strip():
            raise TraderDashboardError("decision ID, instrument and reason are required")
        if self.action not in ACTION_STATES:
            raise TraderDashboardError("decision action must be an explicit supported state")
        if self.decision_at.tzinfo is None or self.decision_at.utcoffset() is None:
            raise TraderDashboardError("decision time must be timezone-aware")
        if self.action == "NO_TRADE" and not (self.blocked_reason or self.reason).strip():
            raise TraderDashboardError("NO_TRADE requires a visible reason")
        values = (self.expected_cost_inr, self.fee_uncertainty_inr, self.stop_distance, self.quantity,
                  self.option_strike, self.bid, self.ask, self.sl_policy_trigger,
                  self.tp_policy_trigger, self.actual_exit_price)
        if any(value is not None and not math.isfinite(value) for value in values):
            raise TraderDashboardError("decision costs, prices and sizing must be finite")
        if self.expected_cost_inr is not None and self.expected_cost_inr < 0:
            raise TraderDashboardError("expected costs cannot be negative")
        if self.fee_uncertainty_inr is not None and self.fee_uncertainty_inr < 0:
            raise TraderDashboardError("fee uncertainty cannot be negative")
        if (self.option_right is not None and self.option_right not in {"CE", "PE"}
                or self.option_lot_size is not None and self.option_lot_size <= 0):
            raise TraderDashboardError("option right and lot size must identify a valid contract")
        option_fields = (self.option_expiry, self.option_strike, self.option_right, self.option_lot_size)
        if any(value is not None for value in option_fields) and any(value is None for value in option_fields):
            raise TraderDashboardError("option proposal needs expiry, strike, right and lot identity")
        if (self.bid is not None or self.ask is not None) and (
                self.bid is None or self.ask is None or self.bid <= 0 or self.ask < self.bid
                or not self.quote_source or self.quote_at is None
                or self.maximum_quote_age_seconds is None):
            raise TraderDashboardError("option quote needs ordered bid/ask, source and quote time")
        if self.maximum_quote_age_seconds is not None and (
                not math.isfinite(self.maximum_quote_age_seconds) or self.maximum_quote_age_seconds <= 0):
            raise TraderDashboardError("maximum option quote age must be finite and positive")
        for moment in (self.quote_at, self.actual_exit_at):
            if moment is not None and (moment.tzinfo is None or moment.utcoffset() is None):
                raise TraderDashboardError("quote and actual-exit times must be timezone-aware")
        if (self.actual_exit_price is None) != (self.actual_exit_at is None):
            raise TraderDashboardError("actual exit price and time must be shown together")
        if self.quote_at is not None and self.quote_at > self.decision_at:
            raise TraderDashboardError("decision cannot use an option quote observed in its future")

    @property
    def option_quote_fresh(self) -> bool | None:
        if self.quote_at is None or self.maximum_quote_age_seconds is None:
            return None
        return (self.decision_at - self.quote_at).total_seconds() <= self.maximum_quote_age_seconds


@dataclass(frozen=True)
class PerformanceAttribution:
    strategy: str
    desk: str
    gross_pnl_inr: float
    fee_leakage_inr: float
    net_pnl_inr: float
    risk_adjusted_benchmark_excess: float | None
    evidence_tier: str

    def validate(self) -> None:
        if not self.strategy.strip() or self.desk not in DESKS:
            raise TraderDashboardError("performance attribution requires a strategy and known desk")
        values = (self.gross_pnl_inr, self.fee_leakage_inr, self.net_pnl_inr,
                  self.risk_adjusted_benchmark_excess)
        if any(value is not None and not math.isfinite(value) for value in values):
            raise TraderDashboardError("attribution P&L and benchmark values must be finite")
        if self.fee_leakage_inr < 0 or not math.isclose(
                self.gross_pnl_inr - self.fee_leakage_inr, self.net_pnl_inr, abs_tol=0.01):
            raise TraderDashboardError("attribution net P&L must reconcile to gross less fee leakage")
        if not self.evidence_tier.strip():
            raise TraderDashboardError("independent evidence tier must be explicit")


@dataclass(frozen=True)
class OperationalIssue:
    code: str
    state: str
    detail: str
    source_sha256: str | None = None

    def validate(self) -> None:
        if not self.code.strip() or not self.detail.strip():
            raise TraderDashboardError("operational issue needs a code and detail")
        if self.state not in {"OPEN", "RESOLVED", "UNKNOWN"}:
            raise TraderDashboardError("operational issue must be OPEN, RESOLVED or UNKNOWN")
        if self.source_sha256 is not None and not _SHA.fullmatch(self.source_sha256):
            raise TraderDashboardError("operational evidence hash must be a lowercase SHA-256")


@dataclass(frozen=True)
class AlertCondition:
    key: str
    kind: str
    active: bool
    detail: str

    def validate(self) -> None:
        kinds = {"ELIGIBLE_SIGNAL", "SL_TRIGGER", "TP_TRIGGER", "RISK_BLOCK",
                 "KILL_SWITCH", "DATA_HEALTH_FAILURE"}
        if not self.key.strip() or self.kind not in kinds or not self.detail.strip():
            raise TraderDashboardError("alert condition requires a stable key, supported kind and detail")


def derive_conservative_alerts(*, previous: tuple[AlertCondition, ...],
                               current: tuple[AlertCondition, ...]) -> tuple[dict[str, str], ...]:
    """Emit only new eligible signals, trigger crossings and risk/data state transitions."""
    for condition in (*previous, *current):
        condition.validate()
    previous_by_key = {item.key: item for item in previous}
    if len(previous_by_key) != len(previous):
        raise TraderDashboardError("previous alert condition keys must be unique")
    current_keys = [item.key for item in current]
    if len(set(current_keys)) != len(current_keys):
        raise TraderDashboardError("current alert condition keys must be unique")
    alerts = []
    for item in current:
        old = previous_by_key.get(item.key)
        if item.kind == "ELIGIBLE_SIGNAL":
            emit = item.active
        elif item.kind in {"SL_TRIGGER", "TP_TRIGGER"}:
            emit = item.active and (old is None or not old.active)
        elif item.kind in {"RISK_BLOCK", "KILL_SWITCH", "DATA_HEALTH_FAILURE"}:
            emit = item.active and (old is None or old.kind != item.kind or not old.active)
        else:
            emit = False
        if emit:
            alerts.append({"key": item.key, "kind": item.kind, "detail": item.detail,
                           "action": "OPERATOR_REVIEW_ONLY"})
    return tuple(alerts)


@dataclass(frozen=True)
class TraceEvent:
    event_id: str
    kind: str
    occurred_at: datetime
    payload_sha256: str
    previous_event_sha256: str | None = None

    def validate(self) -> None:
        if not self.event_id.strip() or self.kind not in {"SIGNAL", "ORDER", "FILL", "EXIT"}:
            raise TraderDashboardError("trace event needs an ID and valid lifecycle kind")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise TraderDashboardError("trace event time must be timezone-aware")
        if not _SHA.fullmatch(self.payload_sha256):
            raise TraderDashboardError("trace event payload must have a lowercase SHA-256")
        if self.previous_event_sha256 is not None and not _SHA.fullmatch(self.previous_event_sha256):
            raise TraderDashboardError("trace predecessor must have a lowercase SHA-256")


@dataclass(frozen=True)
class ResearchHypothesis:
    strategy_id: str
    strategy_version: str
    historical_verdict: str
    forward_tier: str
    failed_variants: tuple[str, ...]
    non_promotion_reason: str

    def validate(self) -> None:
        if not all(value.strip() for value in (self.strategy_id, self.strategy_version,
                                               self.historical_verdict, self.forward_tier,
                                               self.non_promotion_reason)):
            raise TraderDashboardError("hypothesis board requires version, verdict, forward tier and promotion reason")
        if any(not variant.strip() for variant in self.failed_variants):
            raise TraderDashboardError("failed strategy variants cannot be blank")


@dataclass(frozen=True)
class ReplayCandle:
    occurred_at: datetime
    available_at: datetime
    open: float
    high: float
    low: float
    close: float

    def validate(self) -> None:
        if any(value.tzinfo is None or value.utcoffset() is None
               for value in (self.occurred_at, self.available_at)):
            raise TraderDashboardError("replay candle timestamps must be timezone-aware")
        if self.available_at < self.occurred_at:
            raise TraderDashboardError("candle cannot be available before its event time")
        if any(not math.isfinite(value) for value in (self.open, self.high, self.low, self.close)):
            raise TraderDashboardError("replay OHLC values must be finite")
        if self.low <= 0 or self.high < max(self.open, self.close, self.low) or self.low > min(self.open, self.close):
            raise TraderDashboardError("replay OHLC values are inconsistent")


@dataclass(frozen=True)
class ReplayDecision:
    occurred_at: datetime
    decision_id: str
    action: str
    detail: str

    def validate(self) -> None:
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise TraderDashboardError("replay decision timestamp must be timezone-aware")
        if not self.decision_id.strip() or self.action not in ACTION_STATES or not self.detail.strip():
            raise TraderDashboardError("replay decision must expose a valid recommendation state")


def build_operator_replay(*, session_id: str, frame_times: tuple[datetime, ...],
                          candles: tuple[ReplayCandle, ...],
                          decisions: tuple[ReplayDecision, ...]) -> dict[str, object]:
    """Build replay frames using only candles available by each frame's operator time."""
    if not session_id.strip() or not frame_times:
        raise TraderDashboardError("replay needs a session ID and at least one frame")
    if any(moment.tzinfo is None or moment.utcoffset() is None for moment in frame_times):
        raise TraderDashboardError("replay frame times must be timezone-aware")
    if tuple(sorted(frame_times)) != frame_times or len(set(frame_times)) != len(frame_times):
        raise TraderDashboardError("replay frame times must be unique and chronological")
    for candle in candles:
        candle.validate()
    for decision in decisions:
        decision.validate()
    frames = []
    for moment in frame_times:
        known_candles = [c for c in candles if c.available_at <= moment]
        known_decisions = [d for d in decisions if d.occurred_at <= moment]
        frames.append({
            "as_of": moment.astimezone(timezone.utc).isoformat(),
            "candles": [{"occurred_at": c.occurred_at.astimezone(timezone.utc).isoformat(),
                         "open": c.open, "high": c.high, "low": c.low, "close": c.close}
                        for c in sorted(known_candles, key=lambda item: item.occurred_at)],
            "decisions": [{"occurred_at": d.occurred_at.astimezone(timezone.utc).isoformat(),
                           "decision_id": d.decision_id, "action": d.action, "detail": d.detail}
                          for d in sorted(known_decisions, key=lambda item: item.occurred_at)],
        })
    return {"schema": "fb.operator_replay.v1", "session_id": session_id, "frames": frames,
            "physical_orders_enabled": False}


def render_operator_replay(replay: dict[str, object], output: Path) -> Path:
    """Render replay with a time slider; frame data is already point-in-time filtered."""
    if replay.get("schema") != "fb.operator_replay.v1" or not isinstance(replay.get("frames"), list):
        raise TraderDashboardError("validated operator replay frames are required")
    serialized = json.dumps(replay["frames"], ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    serialized = serialized.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    page = ("<!doctype html><meta charset='utf-8'><title>Paper session replay</title>"
            "<h1>Paper session replay — " + html.escape(str(replay["session_id"])) + "</h1>"
            "<p>Point-in-time replay; only candles available by the selected time are shown. Paper only.</p>"
            "<button id='play' type='button'>Play</button><input id='frame' type='range' min='0' value='0'><output id='time'></output>"
            "<pre id='state'></pre><script>const frames=" + serialized + ";"
            "const slider=document.getElementById('frame');slider.max=Math.max(0,frames.length-1);"
            "function show(){const f=frames[Number(slider.value)];document.getElementById('time').textContent=f.as_of;"
            "document.getElementById('state').textContent=JSON.stringify({candles:f.candles,decisions:f.decisions},null,2);}"
            "slider.addEventListener('input',show);let timer=null;document.getElementById('play').onclick=()=>{"
            "if(timer){clearInterval(timer);timer=null;document.getElementById('play').textContent='Play';return;}"
            "document.getElementById('play').textContent='Pause';timer=setInterval(()=>{if(Number(slider.value)>=frames.length-1){"
            "clearInterval(timer);timer=null;document.getElementById('play').textContent='Play';return;}"
            "slider.value=Number(slider.value)+1;show();},700);};show();</script>")
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page, encoding="utf-8")
    return target


@dataclass(frozen=True)
class DashboardHolding:
    instrument: str
    quantity: float
    market_value_inr: float
    unrealized_pnl_inr: float


@dataclass(frozen=True)
class DeskDashboardSnapshot:
    desk: str
    as_of: datetime
    source_mode: str
    source_sha256: str
    newest_quote_age_seconds: float | None
    maximum_quote_age_seconds: float
    risk_policy_status: str
    holdings: tuple[DashboardHolding, ...]
    free_cash_inr: float
    daily_net_pnl_inr: float
    historical_net_pnl_inr: float

    def validate(self) -> None:
        if self.desk not in DESKS:
            raise TraderDashboardError("desk must match the five declared product-charter desks")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise TraderDashboardError("desk snapshot time must be timezone-aware")
        if self.source_mode not in SOURCE_MODES:
            raise TraderDashboardError("source mode must explicitly identify observed, fixture or modeled data")
        if not _SHA.fullmatch(self.source_sha256):
            raise TraderDashboardError("desk source needs a lowercase SHA-256")
        if self.risk_policy_status not in {"PASS", "BLOCK", "UNKNOWN"}:
            raise TraderDashboardError("risk policy state must be PASS, BLOCK or UNKNOWN")
        if (not math.isfinite(self.maximum_quote_age_seconds) or self.maximum_quote_age_seconds <= 0
                or self.newest_quote_age_seconds is not None
                and (not math.isfinite(self.newest_quote_age_seconds) or self.newest_quote_age_seconds < 0)):
            raise TraderDashboardError("quote ages must be finite and within a positive policy")
        numbers = (self.free_cash_inr, self.daily_net_pnl_inr, self.historical_net_pnl_inr,
                   *(value for holding in self.holdings for value in
                     (holding.quantity, holding.market_value_inr, holding.unrealized_pnl_inr)))
        if any(not math.isfinite(value) for value in numbers):
            raise TraderDashboardError("cash, P&L and holding values must be finite")
        if any(not holding.instrument.strip() for holding in self.holdings):
            raise TraderDashboardError("holding instrument identities are required")

    @property
    def quote_health(self) -> str:
        if self.newest_quote_age_seconds is None:
            return "UNKNOWN"
        return "FRESH" if self.newest_quote_age_seconds <= self.maximum_quote_age_seconds else "STALE"


def build_trader_dashboard(*, snapshots: tuple[DeskDashboardSnapshot, ...],
                           maximum_desk_skew_seconds: float = 60.0,
                           forecast: DirectionForecast | None = None,
                           decisions: tuple[DashboardDecision, ...] = (),
                           attribution: tuple[PerformanceAttribution, ...] = (),
                           operational_issues: tuple[OperationalIssue, ...] = (),
                           alerts: tuple[dict[str, str], ...] = (),
                           trace: tuple[TraceEvent, ...] = (),
                           past_snapshot_sha256: str | None = None,
                           hypotheses: tuple[ResearchHypothesis, ...] = ()) -> dict[str, object]:
    """Validate the synchronized five-desk view and hash its snapshot contents."""
    if not math.isfinite(maximum_desk_skew_seconds) or maximum_desk_skew_seconds < 0:
        raise TraderDashboardError("maximum desk clock skew must be finite and non-negative")
    for snapshot in snapshots:
        snapshot.validate()
    if forecast is not None:
        forecast.validate()
    for decision in decisions:
        decision.validate()
    for row in attribution:
        row.validate()
    for issue in operational_issues:
        issue.validate()
    for hypothesis in hypotheses:
        hypothesis.validate()
    for index, event in enumerate(trace):
        event.validate()
        if index == 0 and event.previous_event_sha256 is not None:
            raise TraderDashboardError("first trace event cannot have a predecessor")
        if index > 0 and event.previous_event_sha256 != _trace_digest(trace[index - 1]):
            raise TraderDashboardError("trace events must form an ordered hash-linked chain")
        if index > 0 and event.occurred_at < trace[index - 1].occurred_at:
            raise TraderDashboardError("trace event times must be chronological")
    if past_snapshot_sha256 is not None and not _SHA.fullmatch(past_snapshot_sha256):
        raise TraderDashboardError("past snapshot reference must be a lowercase SHA-256")
    desk_names = [snapshot.desk for snapshot in snapshots]
    if set(desk_names) != DESKS or len(desk_names) != len(DESKS):
        raise TraderDashboardError("dashboard requires exactly one snapshot for each of the five desks")
    newest = max(row.as_of.astimezone(timezone.utc) for row in snapshots)
    oldest = min(row.as_of.astimezone(timezone.utc) for row in snapshots)
    skew = (newest - oldest).total_seconds()
    rows = []
    for snapshot in sorted(snapshots, key=lambda item: item.desk):
        rows.append({
            "desk": snapshot.desk,
            "as_of": snapshot.as_of.astimezone(timezone.utc).isoformat(),
            "source_mode": snapshot.source_mode,
            "source_sha256": snapshot.source_sha256,
            "quote_age_seconds": snapshot.newest_quote_age_seconds,
            "quote_health": snapshot.quote_health,
            "risk_policy_status": snapshot.risk_policy_status,
            "holdings": [{"instrument": holding.instrument, "quantity": holding.quantity,
                          "market_value_inr": holding.market_value_inr,
                          "unrealized_pnl_inr": holding.unrealized_pnl_inr}
                         for holding in snapshot.holdings],
            "free_cash_inr": snapshot.free_cash_inr,
            "daily_net_pnl_inr": snapshot.daily_net_pnl_inr,
            "historical_net_pnl_inr": snapshot.historical_net_pnl_inr,
        })
    body: dict[str, object] = {
        "schema": "fb.trader_dashboard_snapshot.v1",
        "desk_count": len(rows),
        "synchronized": skew <= maximum_desk_skew_seconds,
        "maximum_desk_skew_seconds": maximum_desk_skew_seconds,
        "observed_desk_skew_seconds": skew,
        "global_risk_status": ("BLOCK" if skew > maximum_desk_skew_seconds
                               or any(row["risk_policy_status"] != "PASS" for row in rows)
                               or any(row["quote_health"] != "FRESH" for row in rows)
                               or any(item.option_right is not None and item.option_quote_fresh is not True
                                      for item in decisions) else "PASS"),
        "desks": rows,
        "forecast": None if forecast is None else {
            "as_of": forecast.as_of.astimezone(timezone.utc).isoformat(),
            "p_up": forecast.p_up, "p_down": forecast.p_down, "p_flat": forecast.p_flat,
            "expected_low": forecast.expected_low, "expected_high": forecast.expected_high,
            "model_validity": forecast.model_validity, "calibration_status": forecast.calibration_status,
        },
        "decisions": [{
            "decision_id": item.decision_id, "instrument": item.instrument, "action": item.action,
            "decision_at": item.decision_at.astimezone(timezone.utc).isoformat(), "reason": item.reason,
            "expected_cost_inr": item.expected_cost_inr, "fee_uncertainty_inr": item.fee_uncertainty_inr,
            "stop_distance": item.stop_distance, "quantity": item.quantity,
            "blocked_reason": item.blocked_reason, "option_expiry": item.option_expiry,
            "option_strike": item.option_strike, "option_right": item.option_right,
            "option_lot_size": item.option_lot_size, "bid": item.bid, "ask": item.ask,
            "quote_source": item.quote_source,
            "quote_at": item.quote_at.astimezone(timezone.utc).isoformat() if item.quote_at else None,
            "quote_age_seconds": (item.decision_at - item.quote_at).total_seconds() if item.quote_at else None,
            "maximum_quote_age_seconds": item.maximum_quote_age_seconds,
            "option_quote_status": ("FRESH" if item.option_quote_fresh else "STALE_OR_UNKNOWN"
                                    if item.option_right is not None else "NOT_APPLICABLE"),
            "position_status": "NO_POSITION_STATE_RECORDED",
            "order_status": "NO_ORDER_PLACED",
            "sl_policy_trigger": item.sl_policy_trigger, "tp_policy_trigger": item.tp_policy_trigger,
            "actual_exit_price": item.actual_exit_price,
            "actual_exit_at": item.actual_exit_at.astimezone(timezone.utc).isoformat() if item.actual_exit_at else None,
        } for item in decisions],
        "performance_attribution": [{
            "strategy": row.strategy, "desk": row.desk, "gross_pnl_inr": row.gross_pnl_inr,
            "fee_leakage_inr": row.fee_leakage_inr, "net_pnl_inr": row.net_pnl_inr,
            "risk_adjusted_benchmark_excess": row.risk_adjusted_benchmark_excess,
            "evidence_tier": row.evidence_tier,
        } for row in attribution],
        "operational_issues": [{"code": issue.code, "state": issue.state, "detail": issue.detail,
                                "source_sha256": issue.source_sha256} for issue in operational_issues],
        "alerts": list(alerts),
        "trace": [{"event_id": event.event_id, "kind": event.kind,
                   "occurred_at": event.occurred_at.astimezone(timezone.utc).isoformat(),
                   "payload_sha256": event.payload_sha256,
                   "previous_event_sha256": event.previous_event_sha256,
                   "event_sha256": _trace_digest(event)} for event in trace],
        "past_snapshot_sha256": past_snapshot_sha256,
        "hypotheses": [{"strategy_id": row.strategy_id, "strategy_version": row.strategy_version,
                        "historical_verdict": row.historical_verdict, "forward_tier": row.forward_tier,
                        "failed_variants": list(row.failed_variants),
                        "non_promotion_reason": row.non_promotion_reason} for row in hypotheses],
        "physical_orders_enabled": False,
    }
    body["snapshot_sha256"] = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()
    return body


def render_trader_dashboard(report: dict[str, object], output: Path) -> Path:
    """Render an escaped five-desk overview with visible source and freshness labels."""
    desks = report.get("desks")
    if report.get("schema") != "fb.trader_dashboard_snapshot.v1" or not isinstance(desks, list):
        raise TraderDashboardError("a validated five-desk dashboard snapshot is required")
    cards = []
    for row in desks:
        assert isinstance(row, dict)
        raw_holdings = row.get("holdings")
        if not isinstance(raw_holdings, list) or any(not isinstance(item, dict) for item in raw_holdings):
            raise TraderDashboardError("desk holdings must be a list of validated holding records")
        holdings = "".join("<li>" + html.escape(str(item["instrument"])) + ": "
                           + html.escape(str(item["quantity"])) + " units; value INR "
                           + f"{float(item['market_value_inr']):,.2f}; unrealized INR "
                           + f"{float(item['unrealized_pnl_inr']):,.2f}</li>"
                           for item in raw_holdings)
        cards.append("<section><h2>" + html.escape(str(row["desk"])) + "</h2><p>Source: <b>"
                     + html.escape(str(row["source_mode"])) + "</b> · quote: <b>"
                     + html.escape(str(row["quote_health"])) + "</b> · risk: <b>"
                     + html.escape(str(row["risk_policy_status"])) + "</b></p><p>Quote age seconds: "
                     + html.escape(str(row["quote_age_seconds"])) + " · free cash INR "
                     + f"{float(row['free_cash_inr']):,.2f}</p><p>Daily net P&amp;L INR "
                     + f"{float(row['daily_net_pnl_inr']):,.2f}; historical net P&amp;L INR "
                     + f"{float(row['historical_net_pnl_inr']):,.2f}</p><ul>"
                     + (holdings or "<li>No holdings</li>") + "</ul><code>"
                     + html.escape(str(row["source_sha256"])) + "</code></section>")
    forecast = report.get("forecast")
    forecast_panel = "<section><h2>Market direction</h2><p>NO FORECAST EVIDENCE</p></section>"
    if isinstance(forecast, dict):
        forecast_panel = ("<section><h2>Market direction forecast (not an order)</h2><p>p(UP) "
                          + f"{float(forecast['p_up']):.1%}; p(DOWN) {float(forecast['p_down']):.1%}; "
                          + f"p(FLAT) {float(forecast['p_flat']):.1%}</p><p>Expected range: "
                          + f"{float(forecast['expected_low']):,.2f}–{float(forecast['expected_high']):,.2f}; "
                          + "validity: <b>" + html.escape(str(forecast['model_validity']))
                          + "</b>; calibration: <b>" + html.escape(str(forecast['calibration_status']))
                          + "</b></p></section>")
    decisions = report.get("decisions")
    if not isinstance(decisions, list):
        raise TraderDashboardError("decision panel must be a list")
    decision_rows = []
    for row in decisions:
        if not isinstance(row, dict):
            raise TraderDashboardError("decision panel rows must be objects")
        # Escape all external text and keep forecast/action distinction explicit.
        fields = ("decision_id", "instrument", "action", "reason", "blocked_reason", "option_expiry",
                  "option_right", "quote_source", "quote_at", "quote_age_seconds", "maximum_quote_age_seconds",
                  "option_quote_status", "position_status", "order_status", "bid", "ask", "option_strike",
                  "option_lot_size", "expected_cost_inr", "fee_uncertainty_inr", "stop_distance",
                  "quantity", "sl_policy_trigger", "tp_policy_trigger", "actual_exit_price", "actual_exit_at")
        cells = "".join("<td>" + html.escape(str(row.get(field) if row.get(field) is not None else "—"))
                        + "</td>" for field in fields)
        decision_rows.append("<tr>" + cells + "</tr>")
    columns = ("Decision", "Instrument", "Action", "Reason", "Blocked reason", "Expiry", "Right", "Quote source",
               "Quote time", "Quote age seconds", "Maximum quote age", "Quote status", "Position status", "Order status",
               "Bid", "Ask", "Strike", "Lot", "Expected cost INR", "Fee uncertainty INR",
               "Stop distance", "Quantity", "SL policy trigger", "TP policy trigger", "Actual exit price", "Actual exit time")
    decision_panel = ("<section><h2>Decision states (separate from forecast)</h2><table><thead><tr>"
                      + "".join("<th>" + html.escape(field) + "</th>" for field in columns)
                      + "</tr></thead><tbody>" + ("".join(decision_rows) or "<tr><td colspan='26'>NO_TRADE — no eligible recommendation recorded</td></tr>")
                      + "</tbody></table><p>SL/TP values are policy triggers; actual exits are recorded separately.</p></section>")
    attribution = report.get("performance_attribution")
    if not isinstance(attribution, list):
        raise TraderDashboardError("performance attribution must be a list")
    perf_rows = []
    for row in attribution:
        if not isinstance(row, dict):
            raise TraderDashboardError("performance attribution rows must be objects")
        perf_rows.append("<tr>" + "".join("<td>" + html.escape(str(row.get(key, "—"))) + "</td>"
                          for key in ("strategy", "desk", "gross_pnl_inr", "fee_leakage_inr", "net_pnl_inr",
                                      "risk_adjusted_benchmark_excess", "evidence_tier")) + "</tr>")
    performance_panel = ("<section><h2>Strategy and desk performance (INR)</h2><table><thead><tr>"
                         + "".join("<th>" + label + "</th>" for label in ("Strategy", "Desk", "Gross P&amp;L",
                             "Fee leakage", "Net P&amp;L", "Risk-adjusted benchmark excess", "Evidence tier"))
                         + "</tr></thead><tbody>" + ("".join(perf_rows) or "<tr><td colspan='7'>NO ATTRIBUTION EVIDENCE</td></tr>")
                         + "</tbody></table></section>")
    issues = report.get("operational_issues")
    if not isinstance(issues, list):
        raise TraderDashboardError("operational issues must be a list")
    open_issues = [issue for issue in issues if isinstance(issue, dict) and issue.get("state") != "RESOLVED"]
    issue_items = "".join("<li><b>" + html.escape(str(issue.get("code"))) + "</b>: "
                          + html.escape(str(issue.get("detail"))) + " (" + html.escape(str(issue.get("state"))) + ")</li>"
                          for issue in open_issues)
    issue_panel = ("<section class='incident red'><h2>RED OPERATIONAL ALERTS</h2><ul>" + issue_items
                   + ("" if issue_items else "<li>No unresolved operational issues supplied</li>") + "</ul></section>")
    trace = report.get("trace")
    if not isinstance(trace, list):
        raise TraderDashboardError("trace must be a list")
    trace_rows = "".join("<tr>" + "".join("<td>" + html.escape(str(event.get(key) or "—")) + "</td>"
                        for key in ("event_id", "kind", "occurred_at", "payload_sha256", "previous_event_sha256", "event_sha256")) + "</tr>"
                        for event in trace if isinstance(event, dict))
    past_hash = report.get("past_snapshot_sha256")
    trace_panel = ("<section><h2>Signal → order → fill → exit trace</h2><table><thead><tr>"
                   + "".join("<th>" + label + "</th>" for label in ("Event", "Kind", "Time", "Payload SHA-256", "Previous SHA-256", "Event SHA-256"))
                   + "</tr></thead><tbody>" + (trace_rows or "<tr><td colspan='6'>NO TRACE EVIDENCE</td></tr>")
                   + "</tbody></table><p>Past snapshot SHA-256 comparison reference: "
                   + html.escape(str(past_hash or "not supplied")) + "</p></section>")
    alerts = report.get("alerts")
    if not isinstance(alerts, list):
        raise TraderDashboardError("alerts must be a list")
    alert_lines = []
    for alert in alerts:
        if not isinstance(alert, dict) or alert.get("action") != "OPERATOR_REVIEW_ONLY":
            raise TraderDashboardError("alerts must be explicit operator-review-only records")
        alert_lines.append("<li><b>" + html.escape(str(alert.get("kind"))) + "</b>: "
                           + html.escape(str(alert.get("detail"))) + " (no order action)</li>")
    alert_panel = ("<section><h2>Conservative alerts</h2><ul>" + ("".join(alert_lines)
                   or "<li>No new alert transitions</li>") + "</ul></section>")
    hypotheses = report.get("hypotheses")
    if not isinstance(hypotheses, list):
        raise TraderDashboardError("research hypotheses must be a list")
    hypothesis_rows = []
    for row in hypotheses:
        if not isinstance(row, dict):
            raise TraderDashboardError("research hypothesis rows must be objects")
        hypothesis_rows.append("<tr>" + "".join("<td>" + html.escape(str(value)) + "</td>" for value in (
            row.get("strategy_id"), row.get("strategy_version"), row.get("historical_verdict"),
            row.get("forward_tier"), ", ".join(row.get("failed_variants", [])), row.get("non_promotion_reason"))) + "</tr>")
    hypothesis_panel = ("<section><h2>Research hypothesis board</h2><table><thead><tr>"
                        + "".join("<th>" + title + "</th>" for title in ("Strategy", "Version", "Historical verdict",
                            "Forward tier", "Failed variants", "Non-promotion reason")) + "</tr></thead><tbody>"
                        + ("".join(hypothesis_rows) or "<tr><td colspan='6'>NO HYPOTHESIS EVIDENCE</td></tr>")
                        + "</tbody></table></section>")
    page = ("<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            "<title>Financial Brain trader dashboard</title><style>body{font:15px system-ui;max-width:1300px;margin:2rem auto;padding:0 1rem;background:#f5f7fa;color:#18212b}"
            "main{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:1rem}section{background:#fff;padding:1rem;border:1px solid #d8dee6;border-radius:10px}"
            "code{font-size:11px;overflow-wrap:anywhere}h1{margin-bottom:.2rem}.banner{font-weight:bold;padding:.8rem;background:#f2e7c8}.red{color:#9b1010;background:#ffe4e4}</style><h1>Five-desk paper trader dashboard</h1>"
            f"<p class='banner'>Global risk status: {html.escape(str(report['global_risk_status']))} · physical orders disabled</p><main>"
            + forecast_panel + decision_panel + performance_panel + hypothesis_panel + issue_panel + alert_panel + trace_panel + "".join(cards) + "</main><p>Snapshot SHA-256: <code>"
            + html.escape(str(report["snapshot_sha256"])) + "</code></p></html>")
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page, encoding="utf-8")
    return target
