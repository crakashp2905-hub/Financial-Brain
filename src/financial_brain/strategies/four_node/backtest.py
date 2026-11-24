"""Four-Node event study over every combination of the seven interpretation choices.

Design (research only, paper only):
* Intraday 5-minute bars, one NSE session per day. Fractal nodes (2/2) are built inside a session
  only, so no node spans an overnight gap. SMMA bands, ATR and the 50/100/200 SMMAs run on the
  continuous series. Fibonacci levels come from the previous completed session. Barrier clearance
  applies to barriers *ahead* of the trade only (``rules.barriers_split``, FB-2519).
* An *event* is the first bar whose close crosses N2 (either way) for a given N1-N2-N3 triplet.
  Every predicate any variant needs is computed once per event, so the 128 variants are filters
  over the same events, plus four exit configurations (N4 definition x significant-candle rule).
* Distances are ATR(14) multiples from ``spec.india_v1().params``.
* Entry fills at the next bar's open. Exits are adverse-first: a gap through the stop fills at the
  open, a bar touching both stop and target counts as a stop. The trail uses the bar close. Every
  open trade is flattened at the 15:15 bar close. Costs are the intraday round trip from
  ``costs.india.CostModel`` (mega bucket, with impact).

Interpretation of the exception (source: "If N3 is above or equal to N1, enter when price closes
below N2 without a return candle"): when it applies, the N1 and N3 band checks are waived and the
return-candle rule applies instead; the N2 band, D1, D2, max-SL and barrier checks still apply.
For SELL the trigger is N3 >= N1. For BUY, LITERAL_BELOW keeps the text (trigger N3 >= N1, entry on
a close *below* N2) and MIRROR_ABOVE mirrors the sell rule (trigger N3 <= N1, close above N2).
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np

from financial_brain.strategies.four_node.indicators import FIB_RATIOS, smma
from financial_brain.strategies.four_node.nodes import build
from financial_brain.strategies.four_node.spec import AMBIGUITIES, india_v1

BARS = 75
LAST_ENTRY_BAR = 69      # no new entries after 14:50
FLATTEN_BAR = 72         # 15:15 bar close
TICK = 0.05

CHOICE_IDS = tuple(a.id for a in AMBIGUITIES)


def all_variants() -> list[dict[str, str]]:
    return [dict(zip(CHOICE_IDS, combo, strict=True))
            for combo in itertools.product(*(a.alternatives for a in AMBIGUITIES))]


def variant_code(v: dict[str, str]) -> str:
    """'V' plus seven digits, one per choice in AMBIGUITIES order: the alternative's index."""
    out = []
    for a in AMBIGUITIES:
        out.append(str(a.alternatives.index(v[a.id])))
    return "V" + "".join(out)


def _smma(x: np.ndarray, n: int) -> np.ndarray:
    values = [None if np.isnan(v) else float(v) for v in x]
    result = smma(values, n)
    return np.array([np.nan if v is None else v for v in result], dtype=float)


def _atr(h: np.ndarray, lo: np.ndarray, c: np.ndarray, n: int = 14) -> np.ndarray:
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - lo, np.maximum(abs(h - pc), abs(lo - pc)))
    return _smma(tr, n)


def _sig(o: float, h: float, lo: float, c: float) -> tuple[bool, bool, float]:
    body = abs(c - o)
    up, dn = h - max(o, c), min(o, c) - lo
    return (up > body and dn > body), (up + dn > body), max(up, dn)


@dataclass
class Event:
    day: int
    bar: int
    side: str                 # BUY or SELL (trade side)
    path: str                 # NORMAL or EXC_LITERAL / EXC_MIRROR / EXC_SELL
    base_ok: bool             # N2 band, D1, D2, max SL (variant-independent)
    n1_ok: bool
    n3_le_mid: bool
    n3_ge_mid: bool
    n2_le_high: bool
    n2_ge_high: bool
    ret_since: bool           # no opposite candle since N3
    ret_at: bool              # breakout bar not opposite
    barrier_lh: bool          # barrier clearance with LOW_TO_HIGH fib
    barrier_hl: bool
    n4b_ok_both: bool         # breakout-candle N4 passes wick + significance (BOTH rule)
    n4b_ok_comb: bool
    exits: dict[tuple[int, int], tuple[float, float]]   # (n4 idx, sig idx) -> (net ret, R)


def _simulate(side: str, entry: float, stop: float, target: float, unit: float, o, h, lo, c,
              start: int, sig_rule: int, n4_frac: int, n4_side: int, highs_day, lows_day,
              wick_lim: float, p: dict) -> float:
    """Exit price for one trade from bar ``start`` (entry at its open)."""
    long = side == "BUY"
    sign = 1 if long else -1
    tp_active = True
    trail_start, trail_step = p["trail_start_atr"], p["trail_step_atr"]
    pending_n4 = n4_frac == 0
    for t in range(start, BARS):
        bo, bh, bl, bc = o[t], h[t], lo[t], c[t]
        if t > start:
            if (long and bo <= stop) or (not long and bo >= stop):
                return bo
            if tp_active and ((long and bo >= target) or (not long and bo <= target)):
                return bo
        if (long and bl <= stop) or (not long and bh >= stop):
            return stop
        if tp_active and ((long and bh >= target) or (not long and bl <= target)):
            return target
        if t >= FLATTEN_BAR:
            return bc
        gain = (bc - entry) * sign / unit
        if gain >= trail_start:
            tp_active = False
            locked = trail_step * (1 + int((gain - trail_start) // trail_step))
            new = entry + sign * locked * unit
            if (new - stop) * sign > 0:
                stop = new
        if tp_active:
            both, comb, _ = _sig(bo, bh, bl, bc)
            if (both if sig_rule == 0 else comb):
                return bc
        if pending_n4 and t >= 2 and t + 2 < BARS:
            # a fractal on bar t-2 of the side opposite N3 confirms at bar t
            k = t - 2
            if k >= start:
                if n4_side == 1:
                    is_piv = all(highs_day[k] > highs_day[j] for j in (k - 2, k - 1, k + 1, k + 2)
                                 if 0 <= j < BARS)
                else:
                    is_piv = all(lows_day[k] < lows_day[j] for j in (k - 2, k - 1, k + 1, k + 2)
                                 if 0 <= j < BARS)
                if is_piv:
                    pending_n4 = False
                    both, comb, wick = _sig(o[k], h[k], lo[k], c[k])
                    if wick >= wick_lim or (both if sig_rule == 0 else comb):
                        return bc
    return c[BARS - 1]


def events_for(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray, rt_cost: float
               ) -> list[Event]:
    """All Four-Node events for one instrument; arrays are (days, 75)."""
    p = india_v1().params
    nd = o.shape[0]
    fo, fh, fl, fc = (a.reshape(-1) for a in (o, h, lo, c))
    s_lo, s_mid, s_hi = _smma(fl, 20), _smma(fc, 20), _smma(fh, 20)
    ma = [_smma(fc, n) for n in (50, 100, 200)]
    atr = _atr(fh, fl, fc)
    ratios = np.array(FIB_RATIOS) / 100
    out: list[Event] = []
    for d in range(1, nd):
        if np.isnan(c[d]).any() or np.isnan(c[d - 1]).any():
            continue
        pdl, pdh = float(lo[d - 1].min()), float(h[d - 1].max())
        fib_lh = pdl + (pdh - pdl) * ratios
        fib_hl = pdh - (pdh - pdl) * ratios
        hd, ld = list(h[d]), list(lo[d])
        z = build(hd, ld, bar_ids=[f"session:{d}:bar:{i}" for i in range(BARS)])
        seen: set[tuple[int, int, int, str]] = set()
        for t in range(4, LAST_ENTRY_BAR + 1):
            tri = z.last_three(t)
            if tri is None:
                continue
            n1, n2, n3 = tri
            g = d * BARS + t
            unit = atr[g]
            if np.isnan(unit) or unit <= 0 or np.isnan(s_mid[g]) or np.isnan(ma[2][g]):
                continue
            pat = (n1.side, n2.side, n3.side)
            if pat not in ((1, -1, 1), (-1, 1, -1)):
                continue
            close = float(c[d, t])
            for cross in ("below", "above"):
                if (cross == "below") != (close < n2.price) or close == n2.price:
                    continue
                key = (n1.pivot_at, n2.pivot_at, n3.pivot_at, cross)
                if key in seen:
                    continue
                seen.add(key)
                sell_pat = pat == (1, -1, 1)
                if sell_pat:
                    if cross != "below":
                        continue          # sells only ever break below N2
                    side = "SELL"
                    exc = n3.price >= n1.price
                    path = "EXC_SELL" if exc else "NORMAL"
                else:
                    side = "BUY"
                    if cross == "above":
                        path = "EXC_MIRROR" if n3.price <= n1.price else "NORMAL"
                    else:
                        if not n3.price >= n1.price:
                            continue
                        path = "EXC_LITERAL"
                stop = n3.price + TICK if side == "SELL" else n3.price - TICK
                d1 = abs(n2.price - n3.price) / unit
                d2 = abs(close - n2.price) / unit
                risk = abs(close - stop) / unit
                if side == "SELL":
                    n1_ok = n1.price >= s_hi[g]
                    n2_ok = n2.price <= s_lo[g]
                    n3_le = n3_ge = n3.price <= s_mid[g]
                    n2_le = n2_ge = n2_ok
                else:
                    n1_ok = n1.price <= s_lo[g]
                    n3_le, n3_ge = n3.price <= s_mid[g], n3.price >= s_mid[g]
                    n2_le, n2_ge = n2.price <= s_hi[g], n2.price >= s_hi[g]
                    n2_ok = True
                base = bool(n2_ok and d1 <= p["max_d1_atr"] and d2 < p["max_d2_atr"]
                            and risk <= p["max_sl_atr"] and risk > 0)
                if (side == "BUY" and stop >= close) or (side == "SELL" and stop <= close):
                    base = False
                mas = [m[g] for m in ma]
                lim = p["min_struct_atr"] * unit
                ahead = (lambda b, x=close: b > x) if side == "BUY" else (lambda b, x=close: b < x)
                bar_lh = all(abs(close - b) >= lim for b in list(fib_lh) + mas if ahead(b))
                bar_hl = all(abs(close - b) >= lim for b in list(fib_hl) + mas if ahead(b))
                sgn = 1 if side == "SELL" else -1
                diff = (c[d] - o[d]) * sgn
                ret_since = not bool((diff[n3.pivot_at + 1:t + 1] > 0).any())
                ret_at = not bool(diff[t] > 0)
                both, comb, wick = _sig(o[d, t], h[d, t], lo[d, t], c[d, t])
                wick_ok = wick < p["n4_wick_atr"] * unit
                ev = Event(d, t, side, path, base, bool(n1_ok), bool(n3_le), bool(n3_ge), bool(n2_le),
                           bool(n2_ge), ret_since, ret_at, bar_lh, bar_hl, wick_ok and not both,
                           wick_ok and not comb, {})
                if t + 1 < BARS and base:
                    entry = float(o[d, t + 1])
                    tp = p["tp_atr"] * unit
                    target = entry - tp if side == "SELL" else entry + tp
                    if (side == "SELL" and stop > entry) or (side == "BUY" and stop < entry):
                        r_unit = abs(entry - stop)
                        for n4i in (0, 1):
                            for si in (0, 1):
                                px = _simulate(side, entry, stop, target, unit, o[d], h[d], lo[d],
                                               c[d], t + 1, si, n4i, n2.side, hd, ld,
                                               p["n4_wick_atr"] * unit, p)
                                gross = (px - entry) / entry * (1 if side == "BUY" else -1)
                                ev.exits[(n4i, si)] = (gross - rt_cost,
                                                       (px - entry) * (1 if side == "BUY" else -1) / r_unit)
                out.append(ev)
    return out


def takes(ev: Event, v: dict[str, str]) -> bool:
    """Would variant ``v`` take event ``ev``?"""
    if not ev.base_ok or not ev.exits:
        return False
    exc_literal = v["BUY_EXCEPTION_SIDE"] == "LITERAL_BELOW"
    if ev.path == "EXC_LITERAL" and not exc_literal:
        return False
    if ev.path == "EXC_MIRROR" and exc_literal:
        return False
    if ev.side == "BUY" and ev.path == "NORMAL":
        if not ev.n1_ok:
            return False
        if not (ev.n3_le_mid if v["BUY_N3_MID"] == "LITERAL_LE_MID" else ev.n3_ge_mid):
            return False
        if not (ev.n2_le_high if v["N2_BUY_COMPARATOR"] == "LITERAL_LE_HIGH" else ev.n2_ge_high):
            return False
    elif ev.side == "BUY":
        if not (ev.n2_le_high if v["N2_BUY_COMPARATOR"] == "LITERAL_LE_HIGH" else ev.n2_ge_high):
            return False
    if ev.side == "SELL" and ev.path == "NORMAL" and not (ev.n1_ok and ev.n3_le_mid):
        return False
    if ev.path != "NORMAL":
        if not (ev.ret_since if v["RETURN_CANDLE"] == "NO_OPPOSITE_CANDLE_SINCE_N3" else ev.ret_at):
            return False
    if not (ev.barrier_lh if v["FIB_ANCHOR"] == "PREV_DAY_LOW_TO_HIGH" else ev.barrier_hl):
        return False
    if v["N4_DEFINITION"] == "BREAKOUT_CANDLE":
        if not (ev.n4b_ok_both if v["SIGNIFICANT_CANDLE"] == "BOTH_WICKS_GT_BODY" else ev.n4b_ok_comb):
            return False
    return True


def outcome(ev: Event, v: dict[str, str]) -> tuple[float, float]:
    n4i = 0 if v["N4_DEFINITION"] == "NEXT_OPPOSITE_FRACTAL" else 1
    si = 0 if v["SIGNIFICANT_CANDLE"] == "BOTH_WICKS_GT_BODY" else 1
    return ev.exits[(n4i, si)]
