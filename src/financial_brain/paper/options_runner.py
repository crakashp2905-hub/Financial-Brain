"""Replay explicit long-option intents against archived, observed two-sided quotes.

This module never creates an option signal or calls a broker. An intent must
exist before its entry quote; both fills must be observed before expiry.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from collections import Counter

from .options import OptionPaperError, Quote, cost_hurdle, long_round_trip


@dataclass(frozen=True)
class Intent:
    signal_id: str
    cohort: str
    rule_version: str
    token: int
    decided_at: datetime
    planned_exit_at: datetime
    expected_exit_bid: float
    lots: int = 1
    max_entry_delay: timedelta = timedelta(minutes=1)
    max_exit_delay: timedelta = timedelta(minutes=1)

    def validate(self) -> None:
        if not self.signal_id or not self.cohort or not self.rule_version:
            raise OptionPaperError("signal, cohort and rule version are required")
        if self.decided_at.tzinfo is None or self.planned_exit_at.tzinfo is None:
            raise OptionPaperError("option intent timestamps must be timezone-aware")
        if self.planned_exit_at <= self.decided_at:
            raise OptionPaperError("planned exit must follow the signal")
        if self.max_entry_delay <= timedelta(0) or self.max_exit_delay <= timedelta(0):
            raise OptionPaperError("quote delay limits must be positive")


def run_intent(intent: Intent, quotes: list[Quote]) -> dict:
    """No fill is synthesized when a quote, liquidity or exit is missing."""
    intent.validate()
    base = {"signal_id": intent.signal_id, "cohort": intent.cohort,
            "rule_version": intent.rule_version, "mode": "replay"}
    ordered = sorted((q for q in quotes if q.token == intent.token), key=lambda q: q.observed_at)
    entry = next((q for q in ordered if intent.decided_at < q.observed_at <=
                  intent.decided_at + intent.max_entry_delay and _valid(q)), None)
    if entry is None:
        return {**base, "status": "NO_ENTRY_QUOTE"}
    hurdle = cost_hurdle(entry, lots=intent.lots, expected_exit_bid=intent.expected_exit_bid)
    if not hurdle["passes"]:
        return {**base, "status": "SKIPPED_COST_OR_LIQUIDITY", **hurdle}
    exit_quote = next((q for q in ordered if intent.planned_exit_at <= q.observed_at <=
                       intent.planned_exit_at + intent.max_exit_delay and q.observed_at > entry.observed_at
                       and _valid(q) and q.bid_qty >= intent.lots * q.lot_size
                       and (q.symbol, q.expiry, q.lot_size) ==
                       (entry.symbol, entry.expiry, entry.lot_size)), None)
    if exit_quote is None:
        return {**base, "status": "NO_EXIT_QUOTE", "entry_at": entry.observed_at,
                "entry_premium_inr": entry.ask * entry.lot_size * intent.lots}
    return {**base, "status": "CLOSED", **long_round_trip(entry, exit_quote, lots=intent.lots)}


def _valid(q: Quote) -> bool:
    try:
        q.validate()
    except OptionPaperError:
        return False
    return True


def archive_quotes(con, intent: Intent, *, max_source_lag: timedelta = timedelta(seconds=30)) -> list[Quote]:
    """Read only actually archived quotes; discard delayed source timestamps."""
    intent.validate()
    rows = con.execute("""SELECT received_at, source_at, instrument_token, tradingsymbol, expiry,
                                 lot_size, bid, ask, bid_qty, ask_qty
                          FROM option_quotes WHERE instrument_token = ? AND received_at > ?
                            AND received_at <= ? ORDER BY received_at""",
                       [intent.token, intent.decided_at,
                        intent.planned_exit_at + intent.max_exit_delay]).fetchall()
    out = []
    for received, source, token, symbol, expiry, lot, bid, ask, bid_qty, ask_qty in rows:
        if source is None or source > received or received - source > max_source_lag:
            continue
        out.append(Quote(int(token), symbol, expiry, int(lot), received, float(bid or 0),
                         float(ask or 0), int(bid_qty or 0), int(ask_qty or 0)))
    return out


def replay_cohort(con, intents: list[Intent]) -> dict:
    """Read-only coverage and net-cost report for a frozen set of intents."""
    keys = [(i.cohort, i.rule_version, i.signal_id) for i in intents]
    if len(keys) != len(set(keys)):
        raise OptionPaperError("duplicate cohort/version/signal identity")
    results = [run_intent(i, archive_quotes(con, i)) for i in intents]
    closed = [r for r in results if r["status"] == "CLOSED"]
    counts = Counter(r["status"] for r in results)
    return {"mode": "replay", "intents": len(intents), "closed": len(closed),
            "coverage": len(closed) / len(intents) if intents else 0.0,
            "status_counts": dict(counts),
            "gross_pnl_inr": sum(r["gross_pnl_inr"] for r in closed),
            "charges_inr": sum(r["charges_inr"] for r in closed),
            "net_pnl_inr": sum(r["net_pnl_inr"] for r in closed),
            "results": results}
