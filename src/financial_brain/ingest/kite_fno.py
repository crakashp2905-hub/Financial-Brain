"""F&O intelligence from Kite's read-only instrument list and full quotes: open interest, PCR, max pain,
implied volatility and skew for a stock or index.

Everything is derived from two GETs - the NFO instrument list and a full quote - and nothing here can
place an order. Derivatives positioning is the one input in this system that is *about other people's
positions* rather than prices, which is why it is worth having, and also why it is easy to over-read:

``pcr_oi``      put open interest / call open interest. High means more put positioning, which can be
                hedging as easily as bearishness; it is a measure of positioning, not a forecast.
``max_pain``    the strike at which option writers, in aggregate, pay out least at expiry. A magnet in
                folklore and a weak one in the data; reported, not relied on.
``oi_wall_*``   the strikes with the most call and put open interest: where positioning is concentrated.
``atm_iv``      Black-Scholes implied volatility of the at-the-money straddle, from last traded prices.
``skew``        put IV minus call IV at about 5% out of the money, the market's price of downside.

Implied volatility is solved from the traded price, not read from a feed, so it carries the staleness
of the last trade: an illiquid strike's "implied vol" is whatever it last printed at. Strikes with no
open interest are dropped from the volatility figures; zero-volume strikes are counted for information.
"""
from __future__ import annotations

import math
from datetime import date, datetime

from scipy.optimize import brentq
from scipy.stats import norm

RATE = 0.065            #: a flat risk-free assumption, India's short rate in round terms
MAX_BATCH = 400


class FnoError(ValueError):
    pass


def bs_price(spot: float, strike: float, t: float, vol: float, kind: str, r: float = RATE) -> float:
    if t <= 0 or vol <= 0:
        return max(0.0, (spot - strike) if kind == "CE" else (strike - spot))
    d1 = (math.log(spot / strike) + (r + 0.5 * vol * vol) * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    if kind == "CE":
        return spot * norm.cdf(d1) - strike * math.exp(-r * t) * norm.cdf(d2)
    return strike * math.exp(-r * t) * norm.cdf(-d2) - spot * norm.cdf(-d1)


def implied_vol(price: float, spot: float, strike: float, t: float, kind: str, r: float = RATE):
    """Implied vol from a traded price, or None when the price is outside what any vol can produce."""
    if price <= 0 or spot <= 0 or strike <= 0 or t <= 0:
        return None
    intrinsic = max(0.0, (spot - strike * math.exp(-r * t)) if kind == "CE"
                    else (strike * math.exp(-r * t) - spot))
    if price < intrinsic - 1e-9:
        return None
    f = lambda v: bs_price(spot, strike, t, v, kind, r) - price     # noqa: E731
    try:
        return float(brentq(f, 1e-4, 5.0, xtol=1e-7))
    except ValueError:
        return None


def max_pain(strikes: list[float], call_oi: dict, put_oi: dict) -> float | None:
    """The expiry price minimising total intrinsic value owed to option holders."""
    if not strikes:
        return None
    best, best_cost = None, float("inf")
    for s in strikes:
        cost = sum(max(0.0, s - k) * call_oi.get(k, 0) for k in strikes) \
            + sum(max(0.0, k - s) * put_oi.get(k, 0) for k in strikes)
        if cost < best_cost:
            best, best_cost = s, cost
    return best


def chain(symbol: str, *, spot: float | None = None, today: date | None = None, instrument_list=None,
          quote_fn=None) -> dict:
    """Nearest-expiry option chain for ``symbol`` with last price, OI and volume per strike."""
    from ..providers import kite_data as kd
    quote_fn = quote_fn or kd.quote
    rows = instrument_list if instrument_list is not None else kd.instruments("NFO")
    today = today or date.today()
    opts = [r for r in rows if (r.get("name") or "").strip().upper() == symbol.upper()
            and r.get("instrument_type") in ("CE", "PE")]
    expiries = sorted({r["expiry"] for r in opts if r.get("expiry") and r["expiry"] >= today.isoformat()})
    if not expiries:
        raise FnoError(f"no listed option expiry for {symbol}")
    exp = expiries[0]
    legs = [r for r in opts if r["expiry"] == exp]
    q = {}
    for i in range(0, len(legs), MAX_BATCH):
        part = legs[i:i + MAX_BATCH]
        q.update(quote_fn([f"NFO:{r['tradingsymbol']}" for r in part], "full"))
    if spot is None:
        spot_q = quote_fn([f"NSE:{symbol}"], "ltp")
        spot = next(iter(spot_q.values()))["last_price"]
    out = []
    for r in legs:
        v = q.get(f"NFO:{r['tradingsymbol']}") or {}
        out.append({"strike": float(r["strike"]), "kind": r["instrument_type"], "last": v.get("last_price"),
                    "oi": v.get("oi") or 0, "volume": v.get("volume") or 0,
                    "lot": int(float(r.get("lot_size") or 0))})
    return {"symbol": symbol, "expiry": exp, "spot": spot, "legs": out}


def summarise(ch: dict, *, today: date | None = None, r: float = RATE) -> dict:
    today = today or date.today()
    spot, legs = ch["spot"], ch["legs"]
    t = max((datetime.strptime(ch["expiry"], "%Y-%m-%d").date() - today).days, 0) / 365.0
    if not legs or t <= 0:
        raise FnoError("empty chain or expired")
    strikes = sorted({l["strike"] for l in legs})
    call_oi = {l["strike"]: l["oi"] for l in legs if l["kind"] == "CE"}
    put_oi = {l["strike"]: l["oi"] for l in legs if l["kind"] == "PE"}
    tot_c, tot_p = sum(call_oi.values()), sum(put_oi.values())
    vol_c = sum(l["volume"] for l in legs if l["kind"] == "CE")
    vol_p = sum(l["volume"] for l in legs if l["kind"] == "PE")
    atm = min(strikes, key=lambda k: abs(k - spot))

    def leg(k, kind):
        return next((l for l in legs if l["strike"] == k and l["kind"] == kind), None)

    def iv_at(k, kind):
        l = leg(k, kind)
        # A live contract has open interest and a last price. Volume is zero for every strike outside
        # market hours, so requiring it made every implied vol on a weekend "stale".
        if not l or not l["last"] or l["oi"] <= 0:
            return None
        return implied_vol(l["last"], spot, k, t, kind, r)

    atm_ivs = [x for x in (iv_at(atm, "CE"), iv_at(atm, "PE")) if x]
    otm_put = min(strikes, key=lambda k: abs(k - 0.95 * spot))
    otm_call = min(strikes, key=lambda k: abs(k - 1.05 * spot))
    p_iv, c_iv = iv_at(otm_put, "PE"), iv_at(otm_call, "CE")
    return {
        "symbol": ch["symbol"], "expiry": ch["expiry"], "spot": spot, "days_to_expiry": round(t * 365),
        "pcr_oi": (tot_p / tot_c) if tot_c else None, "pcr_volume": (vol_p / vol_c) if vol_c else None,
        "max_pain": max_pain(strikes, call_oi, put_oi), "atm_strike": atm,
        "atm_iv": (sum(atm_ivs) / len(atm_ivs)) if atm_ivs else None,
        "skew_5pct": (p_iv - c_iv) if (p_iv and c_iv) else None,
        "oi_wall_call": max(call_oi, key=call_oi.get) if call_oi else None,
        "oi_wall_put": max(put_oi, key=put_oi.get) if put_oi else None,
        "total_call_oi": tot_c, "total_put_oi": tot_p,
        "strikes_without_volume": sum(1 for l in legs if l["volume"] <= 0),
        "caveat": "positioning, not prediction; illiquid strikes print stale prices"}
