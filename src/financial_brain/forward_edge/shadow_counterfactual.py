"""Quote-backed hypothetical fills for rejected opportunities, permanently shadow-labeled."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from .durable import DurableJournal


class ShadowCounterfactualError(ValueError):
    """Invalid shadow plan, rejected opportunity, or quote event."""


def _utc(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ShadowCounterfactualError(f"{label} must be a timezone-aware ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ShadowCounterfactualError(f"{label} must be a timezone-aware ISO timestamp")
    return parsed.astimezone(timezone.utc)


def _number(value, label: str, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ShadowCounterfactualError(f"{label} must be finite")
    result = float(value)
    if minimum is not None and result < minimum:
        raise ShadowCounterfactualError(f"{label} must be at least {minimum}")
    return result


@dataclass(frozen=True)
class ShadowFillPolicy:
    policy_id: str
    max_quote_age_seconds: float
    modeled_round_trip_cost_bp: float

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ShadowCounterfactualError("policy_id is required")
        _number(self.max_quote_age_seconds, "max_quote_age_seconds", 0)
        _number(self.modeled_round_trip_cost_bp, "modeled_round_trip_cost_bp", 0)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()


def _validate_quotes(quotes: Sequence[Mapping[str, object]]) -> list[dict]:
    out, seen = [], set()
    for quote in quotes:
        if not isinstance(quote, Mapping):
            raise ShadowCounterfactualError("quote events must be mappings")
        event_id, symbol = quote.get("event_id"), quote.get("symbol")
        if not isinstance(event_id, str) or not event_id.strip() or event_id in seen:
            raise ShadowCounterfactualError("quote event IDs must be unique and nonempty")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ShadowCounterfactualError("quote symbol is required")
        exchange_at = _utc(quote.get("exchange_at_utc"), "exchange_at_utc")
        observed_at = _utc(quote.get("observed_at_utc"), "observed_at_utc")
        if observed_at < exchange_at:
            raise ShadowCounterfactualError("quote observation precedes exchange timestamp")
        bid = _number(quote.get("bid"), "bid", 0)
        ask = _number(quote.get("ask"), "ask", 0)
        bid_size = _number(quote.get("bid_size"), "bid_size", 0)
        ask_size = _number(quote.get("ask_size"), "ask_size", 0)
        if bid <= 0 or ask <= 0 or ask < bid:
            raise ShadowCounterfactualError("quote must be positive and non-crossed")
        if int(bid_size) != bid_size or int(ask_size) != ask_size:
            raise ShadowCounterfactualError("displayed quote sizes must be whole units")
        out.append({"event_id": event_id, "symbol": symbol, "exchange_at": exchange_at,
                    "observed_at": observed_at, "bid": bid, "ask": ask,
                    "bid_size": int(bid_size), "ask_size": int(ask_size)})
        seen.add(event_id)
    return sorted(out, key=lambda row: (row["observed_at"], row["event_id"]))


def _validate_decisions(decisions: Sequence[Mapping[str, object]]) -> list[dict]:
    out, seen = [], set()
    for decision in decisions:
        if not isinstance(decision, Mapping):
            raise ShadowCounterfactualError("rejected decisions must be mappings")
        decision_id = decision.get("decision_id")
        if not isinstance(decision_id, str) or not decision_id.strip() or decision_id in seen:
            raise ShadowCounterfactualError("decision IDs must be unique and nonempty")
        seen.add(decision_id)
        if decision.get("disposition") not in {"REJECTED_RANK", "REJECTED_FILTER", "REJECTED_CASH"}:
            raise ShadowCounterfactualError("only explicitly rejected opportunities enter the shadow book")
        side = decision.get("side")
        if side not in {"BUY", "SELL"}:
            raise ShadowCounterfactualError("side must be BUY or SELL")
        symbol = decision.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ShadowCounterfactualError("symbol is required")
        if not isinstance(decision.get("reason"), str) or not decision["reason"].strip():
            raise ShadowCounterfactualError("rejection reason is required")
        if not isinstance(decision.get("manifest_sha256"), str) or len(decision["manifest_sha256"]) != 64:
            raise ShadowCounterfactualError("manifest_sha256 must be provided")
        for c in decision["manifest_sha256"]:
            if c not in "0123456789abcdef":
                raise ShadowCounterfactualError("manifest_sha256 must be lowercase SHA-256")
        decision_at = _utc(decision.get("decision_at_utc"), "decision_at_utc")
        expiry = _utc(decision.get("entry_deadline_utc"), "entry_deadline_utc")
        exit_at = _utc(decision.get("exit_at_utc"), "exit_at_utc")
        close_deadline = _utc(decision.get("close_deadline_utc"), "close_deadline_utc")
        if not decision_at < expiry <= exit_at < close_deadline:
            raise ShadowCounterfactualError("decision, entry, exit, and close deadlines are inconsistent")
        quantity = _number(decision.get("quantity"), "quantity", 1)
        if int(quantity) != quantity:
            raise ShadowCounterfactualError("quantity must be a whole number")
        limit_price = _number(decision.get("limit_price"), "limit_price", 0)
        if limit_price <= 0:
            raise ShadowCounterfactualError("limit_price must be positive")
        cash_budget = _number(decision.get("cash_budget_inr"), "cash_budget_inr", 0)
        if side == "BUY" and cash_budget <= 0:
            raise ShadowCounterfactualError("buy shadow order needs a positive virtual cash budget")
        out.append({"decision_id": decision_id, "symbol": symbol, "side": side,
                    "disposition": decision["disposition"], "reason": decision["reason"],
                    "manifest_sha256": decision["manifest_sha256"], "decision_at": decision_at,
                    "entry_deadline": expiry, "exit_at": exit_at, "close_deadline": close_deadline,
                    "quantity": int(quantity), "limit_price": limit_price,
                    "cash_budget_inr": cash_budget})
    return sorted(out, key=lambda row: (row["decision_at"], row["decision_id"]))


def simulate_rejected_shadow_book(
    *, decisions: Sequence[Mapping[str, object]], quotes: Sequence[Mapping[str, object]],
    policy: ShadowFillPolicy, registered_control_ids: Sequence[str] = (),
) -> dict:
    """Run a hypothetical quote-depth book; it can never produce observed cash P&L or promotion.

    Marketable limit orders consume only displayed ask (BUY) or bid (SELL) size at
    prices within the frozen limit. Quote depth is shared and consumed once across
    decisions. A later close uses the executable opposite side. Remaining entry or
    close quantity stays explicit; no interpolation or midpoint fill is invented.
    """
    orders = _validate_decisions(decisions)
    events = _validate_quotes(quotes)
    consumed: dict[tuple[str, str], int] = {}
    results = []
    for order in orders:
        entry_lots: list[list[float | int]] = []
        entry_fills = []
        last_entry_at = None
        remaining = order["quantity"]
        cash_remaining = order["cash_budget_inr"]
        for quote in events:
            if quote["symbol"] != order["symbol"] or not order["decision_at"] < quote["observed_at"] <= order["entry_deadline"]:
                continue
            age = (quote["observed_at"] - quote["exchange_at"]).total_seconds()
            if age > policy.max_quote_age_seconds:
                continue
            is_buy = order["side"] == "BUY"
            price = quote["ask"] if is_buy else quote["bid"]
            if (is_buy and price > order["limit_price"]) or (not is_buy and price < order["limit_price"]):
                continue
            size_key = (quote["event_id"], "ASK" if is_buy else "BID")
            available = (quote["ask_size"] if is_buy else quote["bid_size"]) - consumed.get(size_key, 0)
            if is_buy:
                available = min(available, int(cash_remaining // price))
            fill_qty = min(remaining, max(0, available))
            if fill_qty <= 0:
                continue
            entry_lots.append([fill_qty, price])
            last_entry_at = quote["observed_at"]
            if is_buy:
                cash_remaining -= fill_qty * price
            consumed[size_key] = consumed.get(size_key, 0) + fill_qty
            entry_fills.append({"event_id": quote["event_id"], "quantity": fill_qty, "price": price})
            remaining -= fill_qty
            if remaining == 0:
                break
        entered = order["quantity"] - remaining
        entry_notional = sum(fill["quantity"] * fill["price"] for fill in entry_fills)
        entry_vwap = entry_notional / entered if entered else None
        exit_side = "SELL" if order["side"] == "BUY" else "BUY"
        closed = 0
        gross_pnl = 0.0
        modeled_cost = 0.0
        close_fills = []
        close_notional = 0.0
        close_cursor = 0
        for quote in events:
            if entered == 0 or quote["symbol"] != order["symbol"] or not order["exit_at"] <= quote["observed_at"] <= order["close_deadline"]:
                continue
            if last_entry_at is not None and quote["observed_at"] <= last_entry_at:
                continue
            age = (quote["observed_at"] - quote["exchange_at"]).total_seconds()
            if age > policy.max_quote_age_seconds:
                continue
            price = quote["bid"] if exit_side == "SELL" else quote["ask"]
            size_key = (quote["event_id"], "BID" if exit_side == "SELL" else "ASK")
            available = max(0, (quote["bid_size"] if exit_side == "SELL" else quote["ask_size"])
                            - consumed.get(size_key, 0))
            needed = entered - closed
            fill_qty = min(needed, available)
            if fill_qty <= 0:
                continue
            consumed[size_key] = consumed.get(size_key, 0) + fill_qty
            close_fills.append({"event_id": quote["event_id"], "quantity": fill_qty, "price": price})
            close_notional += fill_qty * price
            left = fill_qty
            while left > 0:
                lot_qty, entry_price = entry_lots[close_cursor]
                matched = min(left, int(lot_qty))
                gross_pnl += (price - float(entry_price)) * matched * (1 if order["side"] == "BUY" else -1)
                modeled_cost += float(entry_price) * matched * policy.modeled_round_trip_cost_bp / 1e4
                entry_lots[close_cursor][0] = int(lot_qty) - matched
                left -= matched
                if entry_lots[close_cursor][0] == 0:
                    close_cursor += 1
            closed += fill_qty
            if closed == entered:
                break
        unclosed = entered - closed
        status = ("NO_ENTRY_FILL" if entered == 0 else "UNRESOLVED_CLOSE" if unclosed > 0
                  else "CLOSED" if entered == order["quantity"] else "CLOSED_PARTIAL_ENTRY")
        results.append({"decision_id": order["decision_id"], "symbol": order["symbol"],
                        "rejection_disposition": order["disposition"], "rejection_reason": order["reason"],
                        "status": status, "requested_quantity": order["quantity"],
                        "entered_quantity": entered, "closed_quantity": closed,
                        "unclosed_quantity": unclosed,
                        "entry_vwap": entry_vwap,
                        "close_vwap": close_notional / closed if closed else None,
                        "gross_hypothetical_pnl_inr": gross_pnl if closed else None,
                        "modeled_costs_inr": modeled_cost if closed else None,
                        "net_hypothetical_pnl_inr": gross_pnl - modeled_cost if closed else None,
                        "entry_fills": entry_fills,
                        "close_fills": close_fills,
                        "manifest_sha256": order["manifest_sha256"]})
    if any(not isinstance(value, str) or not value.strip() for value in registered_control_ids):
        raise ShadowCounterfactualError("registered control IDs must be nonempty strings")
    controls = sorted(set(registered_control_ids))
    complete = [row for row in results if row["unclosed_quantity"] == 0 and row["closed_quantity"] > 0]
    incomplete_close = [row for row in results if row["unclosed_quantity"] > 0 and row["closed_quantity"] > 0]
    return {"policy_id": policy.policy_id, "policy_sha256": policy.sha256,
            "evidence_class": "EXPLORATORY_SHADOW", "observed_cash_pnl": False,
            "promotion_eligible": False, "promotion_blockers": (
                [] if controls else ["REGISTERED_CONTROLS_MISSING"]
            ) + ["SHADOW_RESULTS_REQUIRE_SEPARATE_REGISTERED_TRIAL_AND_REVIEW"],
            "registered_control_ids": controls, "order_count": len(results), "orders": results,
            "quote_event_count": len(events), "quote_depth_consumption_is_shared": True,
            "net_hypothetical_pnl_inr": sum(row["net_hypothetical_pnl_inr"] for row in complete),
            "complete_closed_order_count": len(complete),
            "incomplete_close_hypothetical_pnl_inr": sum(
                row["net_hypothetical_pnl_inr"] for row in incomplete_close
            ),
            "unclosed_quantity": sum(row["unclosed_quantity"] for row in results),
            "claim_scope": "Hypothetical quote-depth simulation only; never observed cash P&L or formal forward evidence."}


class ShadowBookJournal:
    """Append-only journal of shadow books; reruns cannot replace a frozen book identity."""

    def __init__(self, path: Path) -> None:
        self.journal = DurableJournal(path)

    def record(self, book_id: str, report: Mapping[str, object]) -> str:
        if not isinstance(book_id, str) or not book_id.strip():
            raise ShadowCounterfactualError("book_id is required")
        if report.get("evidence_class") != "EXPLORATORY_SHADOW" or report.get("observed_cash_pnl") is not False:
            raise ShadowCounterfactualError("shadow book evidence label is mandatory")
        body = {"book_id": book_id, "report": dict(report)}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                       allow_nan=False).encode()).hexdigest()
        record = {"t": "REJECTED_OPPORTUNITY_SHADOW_BOOK", **body, "sha256": digest}
        prior = next((row for row in self.journal.records() if row.get("book_id") == book_id), None)
        if prior:
            if prior != record:
                raise ShadowCounterfactualError("shadow book identity is immutable")
            return digest
        self.journal.append(record)
        return digest
