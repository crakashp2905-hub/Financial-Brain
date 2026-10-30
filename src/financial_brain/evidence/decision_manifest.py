"""Canonical, strict snapshot of a paper research decision.

This is an integrity contract, not independent proof of when a decision existed.
Quantities and fractional returns are decimal strings to avoid cross-language
floating-point serialization drift. No live-order fields or credentials belong here.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


class ManifestError(ValueError):
    pass


TOP = {"schema", "day", "decision_id", "author", "strategy_sha256", "model_sha256",
       "data_sha256", "universe_sha256", "picks_sha256", "calendar_sha256",
       "cost_model_sha256", "risk_limits_sha256", "decision_at_utc",
       "entry_earliest_at_utc", "exit_scheduled_at_utc", "holding_sessions", "benchmark_id",
       "capital_inr", "predicted_return_fraction", "predicted_cost_fraction", "forecast",
       "selections", "exclusions", "sources", "no_trade_reasons"}
SELECTION = {"lineage", "symbol", "weight_fraction", "quantity"}
EXCLUSION = {"lineage", "reason"}
SOURCE = {"source_id", "sha256", "published_at_utc", "received_at_utc"}
FORECAST = {"p10_fraction", "p50_fraction", "p90_fraction", "probability_exceeds_cost"}
DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
SHA = re.compile(r"^[0-9a-f]{64}$")


def _fields(value: Any, expected: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise ManifestError(f"{label}: missing or unexpected fields")
    return value


def _name(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128 or value.strip() != value:
        raise ManifestError(f"invalid {label}")
    return value


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise ManifestError(f"invalid {label}")
    return value


def _fraction(value: Any, label: str, *, nonnegative: bool = False) -> Decimal:
    if not isinstance(value, str) or not DECIMAL.fullmatch(value):
        raise ManifestError(f"{label} must be a decimal string")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ManifestError(f"invalid {label}") from exc
    if not number.is_finite() or (nonnegative and number < 0):
        raise ManifestError(f"invalid {label}")
    return number


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ManifestError(f"invalid {label}")
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ManifestError(f"invalid {label}") from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ManifestError(f"{label} lacks timezone")
    return instant.astimezone(timezone.utc)


def canonical_bytes(value: dict) -> bytes:
    """Validate and serialize a full decision, with order-independent member lists."""
    data = dict(_fields(value, TOP, "manifest"))
    if data["schema"] != "fb.decision.v1":
        raise ManifestError("unsupported manifest schema")
    for key in ("decision_id", "author", "benchmark_id"):
        _name(data[key], key)
    for key in ("strategy_sha256", "model_sha256", "data_sha256", "universe_sha256", "picks_sha256",
                "calendar_sha256", "cost_model_sha256", "risk_limits_sha256"):
        _sha(data[key], key)
    try:
        day = datetime.strptime(data["day"], "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise ManifestError("invalid decision day") from exc
    decision = _utc(data["decision_at_utc"], "decision_at_utc")
    entry = _utc(data["entry_earliest_at_utc"], "entry_earliest_at_utc")
    exit_at = _utc(data["exit_scheduled_at_utc"], "exit_scheduled_at_utc")
    if entry <= decision or exit_at <= entry or decision.astimezone(timezone.utc).date() < day:
        raise ManifestError("decision/entry ordering is invalid")
    if isinstance(data["holding_sessions"], bool) or not isinstance(data["holding_sessions"], int) or data["holding_sessions"] < 1:
        raise ManifestError("invalid holding_sessions")
    _fraction(data["predicted_return_fraction"], "predicted return")
    capital = _fraction(data["capital_inr"], "capital", nonnegative=True)
    if capital <= 0:
        raise ManifestError("capital must be positive")
    cost = _fraction(data["predicted_cost_fraction"], "predicted cost", nonnegative=True)
    if cost >= 1:
        raise ManifestError("predicted cost must be fractional")
    forecast = _fields(data["forecast"], FORECAST, "forecast")
    p10, p50, p90 = (_fraction(forecast[key], key) for key in
                     ("p10_fraction", "p50_fraction", "p90_fraction"))
    probability = _fraction(forecast["probability_exceeds_cost"], "probability", nonnegative=True)
    if p10 > p50 or p50 > p90 or probability > 1:
        raise ManifestError("invalid forecast distribution")
    if not all(isinstance(data[key], list) for key in ("selections", "exclusions", "sources", "no_trade_reasons")):
        raise ManifestError("selection, exclusion, source and abstention fields must be lists")
    weight = Decimal(0)
    for item in data["selections"]:
        _fields(item, SELECTION, "selection")
        _name(item["lineage"], "lineage")
        _name(item["symbol"], "symbol")
        w = _fraction(item["weight_fraction"], "weight", nonnegative=True)
        if w <= 0 or w > 1 or isinstance(item["quantity"], bool) or not isinstance(item["quantity"], int) or item["quantity"] < 1:
            raise ManifestError("invalid selection weight or quantity")
        weight += w
    if weight > 1 or len({x["lineage"] for x in data["selections"]}) != len(data["selections"]):
        raise ManifestError("overallocated or duplicate security lineage")
    for item in data["exclusions"]:
        _fields(item, EXCLUSION, "exclusion")
        _name(item["lineage"], "excluded lineage")
        _name(item["reason"], "exclusion reason")
    if len({x["lineage"] for x in data["exclusions"]}) != len(data["exclusions"]):
        raise ManifestError("duplicate exclusion")
    if {x["lineage"] for x in data["selections"]} & {x["lineage"] for x in data["exclusions"]}:
        raise ManifestError("selected and excluded lineage overlap")
    for reason in data["no_trade_reasons"]:
        _name(reason, "no-trade reason")
    if len(set(data["no_trade_reasons"])) != len(data["no_trade_reasons"]):
        raise ManifestError("duplicate no-trade reason")
    if not data["selections"] and not data["no_trade_reasons"]:
        raise ManifestError("cash decision requires an abstention reason")
    for item in data["sources"]:
        _fields(item, SOURCE, "source")
        _name(item["source_id"], "source ID")
        _sha(item["sha256"], "source digest")
        published = _utc(item["published_at_utc"], "publication")
        received = _utc(item["received_at_utc"], "receipt")
        if published > received or received > decision:
            raise ManifestError("source was unavailable at decision time")
    if not data["sources"] or len({x["source_id"] for x in data["sources"]}) != len(data["sources"]):
        raise ManifestError("missing or duplicate sources")
    for key, sort_key in (("selections", "lineage"), ("exclusions", "lineage"), ("sources", "source_id")):
        data[key] = sorted((dict(row) for row in data[key]), key=lambda row: row[sort_key])
    data["decision_at_utc"] = decision.isoformat().replace("+00:00", "Z")
    data["entry_earliest_at_utc"] = entry.isoformat().replace("+00:00", "Z")
    data["exit_scheduled_at_utc"] = exit_at.isoformat().replace("+00:00", "Z")
    data["no_trade_reasons"] = sorted(data["no_trade_reasons"])
    for source in data["sources"]:
        for key in ("published_at_utc", "received_at_utc"):
            source[key] = _utc(source[key], key).isoformat().replace("+00:00", "Z")
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def digest(value: dict) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()
