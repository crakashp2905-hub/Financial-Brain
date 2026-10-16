"""A cost calculator for one trade or one account: itemised in rupees, with the part nobody can quote exactly
shown as a range, and the tax on a profit shown beside it.

Every number is either an exact charge or an explicitly labelled estimate:

    charges        exact for the rates in :class:`CostModel` (STT, exchange, SEBI, stamp, brokerage, GST, the
                   depository fee on a delivery sell)
    impact + spread  an ESTIMATE, as a range: the low end is the square-root law against the name's own
                   turnover and volatility (when both are given); the high end is the conservative liquidity-
                   bucket assumption. The truth sits in between.
    tax            on a hypothetical profit only, at the rule set in :class:`TaxRules`

It answers the questions a trader has before the order: what do I pay to get in and out, how far must the
price move before I am even, what does each day of holding cost, and how much of a profit do I keep.
Information, not advice.
"""
from __future__ import annotations

from .india import CostModel, Segment, Side
from .tax import TaxRules, classify, tax_on_gain

BUCKETS = ("mega", "large", "mid", "small")
LEGS = ("stt", "exchange", "sebi", "stamp", "brokerage", "gst", "dp")


class CalcError(ValueError):
    pass


def bucket_for(adv_inr: float | None) -> str:
    if not adv_inr:
        return "large"
    return "mega" if adv_inr >= 5e9 else "large" if adv_inr >= 1e9 else "mid" if adv_inr >= 2e8 else "small"


def quote(*, order_inr: float, segment: Segment = Segment.DELIVERY, adv_inr: float | None = None,
          vol_annual: float | None = None, bucket: str | None = None, hold_sessions: int = 20,
          expected_gross_return: float | None = None, rules: TaxRules = TaxRules(),
          model: CostModel | None = None) -> dict:
    if order_inr <= 0:
        raise CalcError("the order value must be positive")
    if segment not in (Segment.DELIVERY, Segment.INTRADAY):
        raise CalcError("this calculator covers cash delivery and intraday")
    model = model or CostModel()
    bucket = bucket or bucket_for(adv_inr)
    if bucket not in BUCKETS:
        raise CalcError(f"bucket must be one of {BUCKETS}")
    buy = model.one_way(turnover=order_inr, side=Side.BUY, segment=segment, bucket=bucket, include_impact=False)
    sell = model.one_way(turnover=order_inr, side=Side.SELL, segment=segment, bucket=bucket, include_impact=False)
    legs = {k: (buy[k], sell[k]) for k in LEGS}
    fees = buy["total"] + sell["total"]
    bucket_imp = 2 * order_inr * model.impact[bucket]
    if adv_inr and vol_annual:
        sq = 2 * model.sqrt_impact(order_value=order_inr, adv_value=adv_inr, daily_vol=vol_annual / 252 ** 0.5) * order_inr
        low, high = min(sq, bucket_imp), max(sq, bucket_imp)
        basis = "square-root law (low) and liquidity bucket (high)"
    else:
        low = high = bucket_imp
        basis = f"liquidity bucket '{bucket}' only (give turnover and volatility for a range)"
    q = {"order_inr": order_inr, "segment": segment.value, "bucket": bucket, "legs": legs, "fees": fees,
         "fees_bp": fees / order_inr * 1e4, "impact_low": low, "impact_high": high, "impact_basis": basis,
         "total_low": fees + low, "total_high": fees + high,
         "bp_low": (fees + low) / order_inr * 1e4, "bp_high": (fees + high) / order_inr * 1e4,
         "breakeven_low": (fees + low) / order_inr, "breakeven_high": (fees + high) / order_inr}
    if hold_sessions and segment != Segment.INTRADAY:
        q["hold_sessions"] = hold_sessions
        q["bp_per_session_low"] = q["bp_low"] / hold_sessions
        q["bp_per_session_high"] = q["bp_high"] / hold_sessions
    if expected_gross_return is not None:
        days = int(hold_sessions * 365 / 252) if segment != Segment.INTRADAY else 0
        kind = classify(days, intraday=segment == Segment.INTRADAY, rules=rules)
        gross = order_inr * expected_gross_return
        out = {"kind": kind, "gross_pnl": gross}
        for label, c in (("low", fees + low), ("high", fees + high)):
            net = gross - c
            t = tax_on_gain(net, kind, rules)
            out[label] = {"cost": c, "net_pre_tax": net, "tax": t, "after_tax": net - t,
                          "kept_pct_of_gross": (net - t) / gross if gross > 0 else float("nan")}
        q["profit"] = out
    return q


def account_table(*, segment: Segment = Segment.DELIVERY, adv_inr: float = 3e9, vol_annual: float = 0.25,
                  sizes=(5_000, 10_000, 25_000, 50_000, 100_000, 500_000, 2_500_000, 10_000_000),
                  model: CostModel | None = None) -> list[dict]:
    """How the cost of one trade changes with its size: where flat fees bite and where impact takes over."""
    rows = []
    for s in sizes:
        q = quote(order_inr=s, segment=segment, adv_inr=adv_inr, vol_annual=vol_annual, hold_sessions=0, model=model)
        rows.append({"order": s, "fees_bp": q["fees_bp"], "impact_bp_low": q["impact_low"] / s * 1e4,
                     "total_bp_low": q["bp_low"], "total_bp_high": q["bp_high"], "total_inr_low": q["total_low"]})
    return rows


def render(q: dict) -> str:
    L = [f"COST OF A ROUND TRIP - {q['segment'].upper()}, order Rs {q['order_inr']:,.0f}, bucket {q['bucket']}",
         f"  {'charge':12s} {'buy':>10s} {'sell':>10s} {'total':>10s}"]
    names = {"stt": "STT", "exchange": "exchange fee", "sebi": "SEBI fee", "stamp": "stamp duty",
             "brokerage": "brokerage", "gst": "GST", "dp": "depository fee"}
    for k in LEGS:
        b, s = q["legs"][k]
        if b or s:
            L.append(f"  {names[k]:12s} Rs {b:8,.2f} Rs {s:8,.2f} Rs {b + s:8,.2f}")
    L.append(f"  {'charges':12s} {'':>10s} {'':>10s} Rs {q['fees']:8,.2f}  = {q['fees_bp']:.1f} bp  (exact)")
    L.append(f"  impact + spread: Rs {q['impact_low']:,.0f} to Rs {q['impact_high']:,.0f}  (estimate; {q['impact_basis']})")
    L.append(f"  TOTAL: Rs {q['total_low']:,.0f} to Rs {q['total_high']:,.0f} = {q['bp_low']:.1f} to {q['bp_high']:.1f} bp "
             f"({q['bp_low'] / 100:.2f}% to {q['bp_high'] / 100:.2f}%)")
    L.append(f"  BREAK-EVEN: the price must move {q['breakeven_low']:.2%} to {q['breakeven_high']:.2%} in your favour before tax")
    if "bp_per_session_low" in q:
        L.append(f"  HOLDING: over {q['hold_sessions']} sessions that is {q['bp_per_session_low']:.2f} to {q['bp_per_session_high']:.2f} bp per session held")
    if "profit" in q:
        p = q["profit"]
        L.append(f"  IF THE POSITION GAINS {p['gross_pnl'] / q['order_inr']:.1%} (Rs {p['gross_pnl']:,.0f}), taxed as {p['kind']}:")
        for lab in ("low", "high"):
            x = p[lab]
            L.append(f"    {lab:4s} cost: costs Rs {x['cost']:,.0f} -> Rs {x['net_pre_tax']:,.0f} before tax -> tax Rs {x['tax']:,.0f} "
                     f"-> Rs {x['after_tax']:,.0f} kept ({x['kept_pct_of_gross']:.0%} of the gross gain)")
    L.append("  Information, not advice. Tax rules are parameters; check the current schedule and your own position.")
    return "\n".join(L)
