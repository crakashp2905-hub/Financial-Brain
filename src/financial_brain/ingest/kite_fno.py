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


# ============================================================================ positioning, day over day
LONG_BUILDUP, SHORT_BUILDUP, SHORT_COVERING, LONG_UNWINDING, NEUTRAL = (
    "LONG_BUILDUP", "SHORT_BUILDUP", "SHORT_COVERING", "LONG_UNWINDING", "NEUTRAL")
SNAPSHOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS fno_snapshots (
    day VARCHAR, symbol VARCHAR, spot DOUBLE, fut DOUBLE, fut_expiry VARCHAR, fut_oi DOUBLE, basis DOUBLE,
    basis_annualised DOUBLE, atm_iv DOUBLE, skew_5pct DOUBLE, pcr_oi DOUBLE, max_pain DOUBLE,
    PRIMARY KEY (day, symbol));
"""


def classify_buildup(price_change: float, oi_change: float, *, min_price: float = 0.002, min_oi: float = 0.01) -> str:
    """The standard reading of price and open interest together. A move below the thresholds is NEUTRAL: a 0.1%
    price change with a 0.3% OI change is noise, not positioning."""
    if abs(price_change) < min_price or abs(oi_change) < min_oi:
        return NEUTRAL
    if price_change > 0:
        return LONG_BUILDUP if oi_change > 0 else SHORT_COVERING
    return SHORT_BUILDUP if oi_change > 0 else LONG_UNWINDING


def futures_snapshot(symbols, *, instrument_list, quote_fn, today: date | None = None) -> dict:
    """Near-month future and spot for each symbol: price, open interest, the basis (annualised), and the rollover -
    the next month's share of near + next open interest (traders moving positions forward before expiry)."""
    today = today or date.today()
    by_name: dict[str, list] = {}
    for r in instrument_list:
        if r.get("instrument_type") == "FUT" and (r.get("name") or "").upper() in symbols and r.get("expiry", "") >= today.isoformat():
            by_name.setdefault(r["name"].upper(), []).append(r)
    futs = {k: sorted(v, key=lambda r: r["expiry"])[0] for k, v in by_name.items()}
    nxt = {k: sorted(v, key=lambda r: r["expiry"])[1] for k, v in by_name.items() if len(v) > 1}
    if not futs:
        return {}
    fq, sq = {}, {}
    names = list(futs)
    for i in range(0, len(names), MAX_BATCH):
        part = names[i:i + MAX_BATCH]
        fq.update(quote_fn([f"NFO:{futs[s]['tradingsymbol']}" for s in part]
                           + [f"NFO:{nxt[s]['tradingsymbol']}" for s in part if s in nxt], "full"))
        sq.update(quote_fn([f"NSE:{s}" for s in part], "ltp"))
    out = {}
    for s in names:
        f, sp = fq.get(f"NFO:{futs[s]['tradingsymbol']}") or {}, (sq.get(f"NSE:{s}") or {}).get("last_price")
        if not f.get("last_price") or not sp:
            continue
        days = max((datetime.strptime(futs[s]["expiry"], "%Y-%m-%d").date() - today).days, 1)
        basis = f["last_price"] / sp - 1.0
        near_oi = float(f.get("oi") or 0)
        next_oi = float((fq.get(f"NFO:{nxt[s]['tradingsymbol']}") or {}).get("oi") or 0) if s in nxt else None
        out[s] = {"spot": float(sp), "fut": float(f["last_price"]), "fut_expiry": futs[s]["expiry"],
                  "fut_oi": near_oi, "basis": basis, "basis_annualised": basis * 365 / days,
                  "rollover": next_oi / (near_oi + next_oi) if next_oi is not None and near_oi + next_oi > 0 else None}
    return out


def save_snapshot(con, day: str, rows: dict[str, dict]) -> int:
    con.execute(SNAPSHOT_SCHEMA)
    con.execute("ALTER TABLE fno_snapshots ADD COLUMN IF NOT EXISTS rollover DOUBLE")      # added 2026-10-07
    for s, r in rows.items():
        con.execute("""INSERT OR REPLACE INTO fno_snapshots (day, symbol, spot, fut, fut_expiry, fut_oi, basis, basis_annualised,
                       atm_iv, skew_5pct, pcr_oi, max_pain, rollover) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [day, s, r.get("spot"), r.get("fut"), r.get("fut_expiry"), r.get("fut_oi"), r.get("basis"),
                     r.get("basis_annualised"), r.get("atm_iv"), r.get("skew_5pct"), r.get("pcr_oi"), r.get("max_pain"),
                     r.get("rollover")])
    return len(rows)


def positioning(con, day: str, *, min_history: int = 20) -> list[dict]:
    """Each symbol's build-up against its previous snapshot, and its implied-vol percentile once there is history."""
    con.execute(SNAPSHOT_SCHEMA)
    rows = con.execute("""
        WITH t AS (SELECT * FROM fno_snapshots WHERE day = ?),
             p AS (SELECT symbol, ARG_MAX(fut, day) AS fut, ARG_MAX(fut_oi, day) AS fut_oi, ARG_MAX(fut_expiry, day) AS exp
                   FROM fno_snapshots WHERE day < ? GROUP BY symbol)
        SELECT t.symbol, t.fut, p.fut, t.fut_oi, p.fut_oi, t.fut_expiry, p.exp, t.basis_annualised, t.atm_iv
        FROM t LEFT JOIN p USING (symbol)""", [day, day]).fetchall()
    out = []
    for sym, fut, pfut, oi, poi, exp, pexp, basis, iv in rows:
        rolled = pexp is not None and exp != pexp            # a new contract: OI is not comparable across a roll
        pc = (fut / pfut - 1.0) if pfut else None
        oc = (oi / poi - 1.0) if poi and not rolled else None
        hist = [r[0] for r in con.execute("SELECT atm_iv FROM fno_snapshots WHERE symbol = ? AND atm_iv IS NOT NULL AND day <= ?",
                                          [sym, day]).fetchall()]
        ivp = (sum(1 for h in hist if h <= iv) / len(hist)) if iv is not None and len(hist) >= min_history else None
        out.append({"symbol": sym, "price_change": pc, "oi_change": oc,
                    "buildup": classify_buildup(pc, oc) if pc is not None and oc is not None else ("ROLLED" if rolled else None),
                    "basis_annualised": basis, "atm_iv": iv, "iv_percentile": ivp,
                    "iv_history_days": len(hist)})
    return out


def combined(ledger, cs, day: str):
    """Spot and F&O in one frame: the cross-section's spot features joined with each F&O name's build-up, futures
    basis and implied-vol percentile for ``day``. Names without F&O keep NaN, so a model sees 'no derivatives'
    rather than a fabricated neutral. Pre-registered test: 'PRE-REGISTERED F&O positioning with spot', run once
    60 sessions of snapshots exist."""
    import pandas as pd
    pos = pd.DataFrame(positioning(ledger, day))
    if pos.empty:
        return cs.assign(buildup=None, basis_annualised=float("nan"), iv_percentile=float("nan"), oi_change=float("nan"))
    pos = pos.rename(columns={"symbol": "ticker"})[["ticker", "buildup", "basis_annualised", "iv_percentile", "oi_change"]]
    return cs.merge(pos, on="ticker", how="left")
