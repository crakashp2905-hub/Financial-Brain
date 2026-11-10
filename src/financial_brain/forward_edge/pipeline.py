"""Forward session pipeline: data -> decision -> paper execution -> ledger -> proof (FE-0143 .. FE-0148). Offline fixtures only.

One session is one dict in, one result dict out. The same pieces the earlier waves built are used, not copied:
preflight and abstention (session_ops, market_data), conservative fills (execution), the double-entry ledger
(kernel), the decision manifest and journal (decision_custody) and the custody classification (custody). Custody is
LOCAL_ONLY here because no independent timestamp provider exists offline; that is never promotable.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal

from financial_brain.forward_edge import decision_custody as DC
from financial_brain.forward_edge import execution as X
from financial_brain.forward_edge import session_ops as S
from financial_brain.forward_edge.modes import Mode, SessionMode, resolve_mode
from financial_brain.kernel.ledger import CREDIT, DEBIT, DoubleEntryLedger, Entry, Transaction
from financial_brain.kernel.units import Money

CCY = "INR"
CENT = Decimal("0.01")
OUTCOMES = ("WIN", "LOSS", "NO_TRADE", "REJECTED", "LATE_QUOTE", "UNKNOWN_ORDER", "MISSED_FILL", "FLAT")


def _paise(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_EVEN)


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)


def run_session(fx: dict, *, mode: Mode = Mode.LIVE_PAPER, broker: str = "NONE") -> dict:
    """Run one fixture session in paper mode. Refuses any mode that could reach a broker or cash."""
    session = resolve_mode(mode, now=_utc(fx["decision_utc"]))
    if not isinstance(session, SessionMode) or mode in (Mode.LIVE_CASH, Mode.BROKER_DRYRUN) or broker != "NONE":
        raise PermissionError("the pipeline runs paper only with no broker")
    trace: list[dict] = []
    ledger = DoubleEntryLedger()
    sid = fx["session_id"]
    decision, entry_earliest = _utc(fx["decision_utc"]), _utc(fx["entry_earliest_utc"])
    segments = {"preflight"}
    journal = DC.ManifestJournal()

    report = S.run_preflight(fx.get("preflight", {k: True for k in S.REQUIRED_PREFLIGHT}), decision)
    trace.append({"stage": "preflight", "ok": report.ok, "failures": list(report.failures)})
    outcome, reason = "NO_TRADE", "PREFLIGHT_BLOCKED"
    net = Decimal(0)

    if report.ok:
        manifest = DC.DecisionManifest(
            session_id=sid, strategy_version_sha256=fx["strategy_sha256"], snapshot_sha256=fx["snapshot_sha256"],
            decision_at_utc=decision.isoformat(), entry_earliest_at_utc=entry_earliest.isoformat(), side=fx["side"],
            instrument_id=fx["instrument"], intended_quantity=int(fx["quantity"]), risk_policy_sha256=fx["risk_sha256"],
            benchmark_id=fx["benchmark_id"], evidence_tier="LOCAL_PAPER")
        journal.record(manifest, recorded_at_utc=decision + timedelta(seconds=1))
        segments.add("decisions")
        trace.append({"stage": "manifest", "sha256": manifest.sha256(), "custody": journal.status(sid, {}).value})

        quotes = [X.Quote(decision + timedelta(milliseconds=q["offset_ms"]), Decimal(q["bid"]), Decimal(q["ask"]),
                          int(q["bid_qty"]), int(q["ask_qty"])) for q in fx["quotes"]]
        segments.add("quotes")
        intent = X.OrderIntent(fx["instrument"], fx["side"], int(fx["quantity"]), decision, Decimal(fx["tick"]),
                               Decimal(fx["limit"]) if fx.get("limit") else None)
        latency = timedelta(milliseconds=int(fx.get("latency_ms", 200)))
        feed = fx.get("feed", {"ok": True, "reasons": []})
        first = X.earliest_quote(quotes, decision, latency)
        late = first is not None and (first.available_utc - decision) > timedelta(milliseconds=int(fx.get("max_wait_ms", 5000)))

        if fx.get("ack_lost"):
            outcome, reason = "UNKNOWN_ORDER", "ACK_LOST_NO_RESEND"
            trace.append({"stage": "order", "state": "UNKNOWN", "resend_allowed": False})
            segments.add("orders")
        elif late:
            outcome, reason = "LATE_QUOTE", "FIRST_QUOTE_AFTER_MAX_WAIT"
            trace.append({"stage": "order", "state": "NO_ORDER", "reason": reason})
        else:
            if fx.get("passive"):
                trades = [(decision + timedelta(milliseconds=t["offset_ms"]), Decimal(t["price"]), int(t["qty"]))
                          for t in fx.get("trades", [])]
                res = X.passive_limit(intent, trades, latency)
                if fx.get("adverse_bps"):
                    res = X.apply_adverse_selection(res, intent.side, Decimal(fx["adverse_bps"]))
            else:
                band = (Decimal(fx["price_band"][0]), Decimal(fx["price_band"][1])) if fx.get("price_band") else None
                if not feed.get("ok", True):
                    res = X.execute_or_no_trade(False, feed.get("reasons", []), intent, quotes, latency)
                else:
                    res = X.marketable_limit(intent, quotes, latency, band)
            segments.update({"orders"})
            trace.append({"stage": "execution", "status": res.status, "filled": res.filled_qty,
                          "price": str(res.avg_price) if res.avg_price is not None else None, "reason": res.reason,
                          "fidelity": res.fidelity})
            if res.status in ("FILLED", "PARTIAL"):
                qty, px = res.filled_qty, res.avg_price
                notional = _paise(px * qty)
                fee_rate = Decimal(fx.get("fee_bps", "3")) / Decimal(10_000)
                entry_fee = _paise(notional * fee_rate)
                close_px = Decimal(fx["close_price"])
                exit_value = _paise(close_px * qty)
                exit_fee = _paise(exit_value * fee_rate)
                sign = 1 if fx["side"] == "BUY" else -1
                gross = (exit_value - notional) * sign
                ledger.post(Transaction(f"{sid}:entry", (Entry("position", DEBIT, Money.of(notional, CCY)),
                                                          Entry("cash", CREDIT, Money.of(notional, CCY))))
                            if sign == 1 else Transaction(f"{sid}:entry", (Entry("cash", DEBIT, Money.of(notional, CCY)),
                                                                          Entry("position", CREDIT, Money.of(notional, CCY)))))
                for name, fee in (("entry_fee", entry_fee), ("exit_fee", exit_fee)):
                    if fee > 0:
                        ledger.post(Transaction(f"{sid}:{name}", (Entry("fees", DEBIT, Money.of(fee, CCY)),
                                                                   Entry("cash", CREDIT, Money.of(fee, CCY)))))
                if sign == 1:
                    cash_in, pos_out = exit_value, notional
                    legs = [Entry("cash", DEBIT, Money.of(cash_in, CCY)), Entry("position", CREDIT, Money.of(pos_out, CCY))]
                else:
                    legs = [Entry("position", DEBIT, Money.of(notional, CCY)), Entry("cash", CREDIT, Money.of(exit_value, CCY))]
                pnl_leg = exit_value - notional if sign == 1 else notional - exit_value
                if pnl_leg > 0:
                    legs.append(Entry("realized_pnl", CREDIT, Money.of(pnl_leg, CCY)))
                elif pnl_leg < 0:
                    legs.append(Entry("realized_pnl", DEBIT, Money.of(-pnl_leg, CCY)))
                ledger.post(Transaction(f"{sid}:exit", tuple(legs)))
                net = gross - entry_fee - exit_fee
                outcome = "WIN" if net > 0 else ("LOSS" if net < 0 else "FLAT")
                reason = "FILLED_" + res.status
                segments.update({"fills", "ledger"})
                trace.append({"stage": "ledger", "net_pnl": str(net), "balanced": ledger.trial_balance_ok(),
                              "cash": str(ledger.balance("cash", CCY))})
            elif res.status == "REJECTED":
                outcome, reason = "REJECTED", res.reason
            elif res.status == "NO_TRADE":
                outcome, reason = "NO_TRADE", res.reason
            else:
                outcome, reason = "MISSED_FILL", res.reason
    segments.update({"close", "reconciliation"})
    custody = journal.status(sid, {}).value if report.ok else "NOT_RECORDED"
    result = {"session_id": sid, "outcome": outcome, "reason": reason, "net_pnl": str(net), "custody": custody,
              "mode": session.mode.value, "broker": "NONE", "physical_orders": False,
              "ledger_balanced": ledger.trial_balance_ok(),
              "cash_equals_net": ledger.balance("cash", CCY) == net if outcome in ("WIN", "LOSS", "FLAT") else True,
              "segments": sorted(segments), "trace": trace}
    result["signature"] = hashlib.sha256(_canon({k: v for k, v in result.items()}).encode("utf-8")).hexdigest()
    return result


def close_session(result: dict, *, required_segments: tuple[str, ...] = S.SEGMENTS, incidents: list[str] | None = None) -> dict:
    """Idempotent. Incomplete evidence is never closed as verified; LOCAL_ONLY custody is never verified either."""
    present = set(result["segments"])
    # sessions that legitimately never traded still close; only trading sessions need every segment
    needed = tuple(s for s in required_segments if result["outcome"] in ("WIN", "LOSS", "FLAT") or s in ("preflight", "close", "reconciliation"))
    rep = S.completeness_report(result["session_id"], present, list(incidents or []))
    missing = [s for s in needed if s not in present]
    status = "CLOSED_INCOMPLETE" if missing or incidents else "CLOSED"
    return {"session_id": result["session_id"], "status": status, "missing": missing,
            "verified": result["custody"] == "VERIFIED" and status == "CLOSED",
            "eligibility": "ELIGIBLE_LOCAL_ONLY" if status == "CLOSED" and result["custody"] != "VERIFIED" else rep["eligibility"]}


def preflight_report(results: dict[str, bool], now: datetime) -> tuple[dict, int]:
    rep = S.run_preflight(results, now)
    return ({"ok": rep.ok, "failures": list(rep.failures)}, 0 if rep.ok else 2)
