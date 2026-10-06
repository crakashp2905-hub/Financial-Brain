"""The trader's view of an opportunity: why this, why now, what is the edge, what if I'm wrong, how much, how to
enter, what is the case against - and what was watched but not traded.

Every number here is one the system measured or computed from its own data. Where the evidence is weak the panel
says so in words ("Weak", "n.a."), and where nothing clears costs the page says that before anything else: a
trader view that dressed up a +0.2% expectation as a confident call would be the most dangerous thing in the
product.
"""
from __future__ import annotations

import html
import json

import numpy as np
import pandas as pd
from scipy.stats import norm

#: The composite's four inputs, with the words a trader would use for each.
COMPONENTS = (("vol_60", -1, "low volatility"), ("mom_12_1", +1, "12-month momentum"),
              ("dist_52w_high", +1, "near its 52-week high"), ("ret_20d", -1, "1-month pullback"))
EXTENDED_20D = 0.15          #: a 20-session gain beyond this is flagged as extended
RSI_HOT = 70.0


def _e(x) -> str:
    return html.escape("" if x is None else str(x))


def components(cs: pd.DataFrame) -> pd.DataFrame:
    """Each name's signed rank-normal score on each composite input (positive = helps the rank)."""
    out = pd.DataFrame(index=cs.index)
    for col, sign, _ in COMPONENTS:
        r = cs[col].rank(pct=True)
        n = r.notna().sum()
        out[col] = sign * norm.ppf((r * n - 0.5) / n)
    return out


def why_this(row: pd.Series, comp: pd.Series, rank: int, universe: int) -> list[str]:
    """Why this name ranks where it does among the whole liquid universe."""
    parts = sorted(((comp[c], label) for c, _, label in COMPONENTS if pd.notna(comp.get(c))), reverse=True)
    pct = 1 - (rank - 1) / max(universe, 1)
    out = [f"ranked {rank} of {universe} liquid names by expected net alpha (top {1 - pct + 1 / universe:.1%})"]
    helps = [f"{label} (z {z:+.1f})" for z, label in parts if z > 0.5]
    hurts = [f"{label} (z {z:+.1f})" for z, label in parts if z < -0.5]
    if helps:
        out.append("lifted by " + ", ".join(helps))
    if hurts:
        out.append("held back by " + ", ".join(hurts))
    return out


def why_now(row: pd.Series) -> list[tuple[bool, str]]:
    """A checklist: (True = supports the trade now, False = a caution)."""
    out = []
    surge = row["turnover"] / row["adv20"] if row.get("adv20") else np.nan
    if pd.notna(row.get("dist_52w_high")):
        out.append((row["dist_52w_high"] > -0.05, f"{row['dist_52w_high'] * 100:+.1f}% from its 52-week high"))
    if pd.notna(row.get("mom_12_1")):
        out.append((row["mom_12_1"] > 0, f"12-month momentum {row['mom_12_1'] * 100:+.0f}%"))
    if pd.notna(surge):
        out.append((surge >= 1.0, f"turnover {surge:.1f}x its 20-day average"))
    if pd.notna(row.get("ret_20d")):
        ext = row["ret_20d"] > EXTENDED_20D
        out.append((not ext, f"{row['ret_20d'] * 100:+.1f}% over 20 sessions" + (" - extended" if ext else "")))
    if pd.notna(row.get("rsi_14")):
        hot = row["rsi_14"] > RSI_HOT
        out.append((not hot, f"RSI {row['rsi_14']:.0f}" + (" - overbought" if hot else "")))
    if pd.notna(row.get("atr_pctile_250")):
        exp = row["atr_pctile_250"] >= 0.9
        out.append((not exp, f"daily range at its {row['atr_pctile_250']:.0%} percentile of the year"
                             + (" - volatility expanding, which has preceded underperformance" if exp else "")))
    return out


def strength(value: float | None, strong: float, moderate: float) -> str:
    if value is None or not np.isfinite(value):
        return "n.a."
    return "Strong" if value >= strong else "Moderate" if value >= moderate else "Weak" if value >= 0 else "Negative"


def edge_panel(row: pd.Series, *, decile_mean: float | None, analogue: dict | None, breadth_state: str | None,
               fno: dict | None, bucket: str, ep_pct: float | None, fund: dict | None = None) -> list[tuple[str, str, str]]:
    """(input, strength word, the measured number behind it)."""
    out = [("Signal (composite decile)", strength(decile_mean, 0.004, 0.002),
            f"{decile_mean * 100:+.2f}% a 20-session excess for its decile, 2016-26 (in-sample) - strong relative to other deciles, small in absolute terms" if decile_mean is not None else "-")]
    if analogue:
        h5 = analogue["h5"]
        out.append(("Historical analogues", strength(h5["edge_t"], 2.0, 1.0),
                    f"{analogue['k']} similar situations: 5-day {h5['mean'] * 100:+.2f}% vs pool {h5['pool_mean'] * 100:+.2f}%, "
                    f"win {h5['win_rate']:.0%}, edge t {h5['edge_t']:+.1f}"))
    else:
        out.append(("Historical analogues", "n.a.", "pool unavailable"))
    if fund and fund.get("latest_period"):
        def fmt(key, spec, scale=1.0):
            v = fund.get(key)
            return "-" if v is None else format(v * scale, spec)
        out.append(("Fundamental", "Weak",
                    f"P/E {fmt('pe', '.1f')}, P/B {fmt('pb', '.1f')}, EV/EBITDA {fmt('ev_ebitda', '.1f')}, "
                    f"ROE {fmt('roe', '.0f', 100)}%, debt/equity {fmt('debt_equity', '.2f')} - at today's price on results "
                    f"to {fund['latest_period']} (stale: later results not ingested); fundamentals were faint in both test samples"))
    else:
        out.append(("Fundamental", "n.a.", "no filing data for this name"))
    out.append(("Market regime", {"broad": "Strong", "normal": "Moderate"}.get((breadth_state or "").lower(), "Weak"),
                f"breadth {breadth_state}" if breadth_state else "-"))
    if fno:
        from ..ingest import kite_fno as KF
        b = fno.get("buildup")
        word = "Moderate" if b in (KF.LONG_BUILDUP, KF.SHORT_COVERING) else "Negative" if b in (KF.SHORT_BUILDUP, KF.LONG_UNWINDING) else "Weak"
        basis = fno.get("basis_annualised")
        out.append(("F&O positioning", word, f"{b or 'no change yet'}" + (f"; futures basis {basis * 100:+.1f}% a year" if basis is not None else "")
                    + "; untested as a signal (snapshots began 2026-10-05)"))
    else:
        out.append(("F&O positioning", "n.a.", "not an F&O name, or no snapshot yet"))
    out.append(("Liquidity", {"mega": "Excellent", "large": "Good", "mid": "Adequate"}.get(bucket, "Thin"), f"{bucket} bucket"))
    return out


def outcome(returns: np.ndarray, stop_pct: float) -> dict:
    """From a distribution of horizon returns, with the stop as the worst loss: P(up), the average gain if right,
    the average loss if wrong, and their ratio."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) == 0:
        return {}
    r = np.maximum(r, -stop_pct)
    up, down = r[r > 0], r[r <= 0]
    gain = float(up.mean()) if len(up) else 0.0
    loss = float(-down.mean()) if len(down) else 0.0
    return {"p_up": float((r > 0).mean()), "gain_if_right": gain, "loss_if_wrong": loss,
            "reward_risk": gain / loss if loss > 0 else float("inf"), "p10": float(np.quantile(r, .1)),
            "p50": float(np.median(r)), "p90": float(np.quantile(r, .9))}


def bear_case(row: pd.Series, analogue: dict | None, breadth_state: str | None) -> list[str]:
    out = []
    if analogue:
        out.append(f"{1 - analogue['h5']['win_rate']:.0%} of the {analogue['k']} most similar past situations were down "
                   f"5 sessions later (median worst point {analogue['mae5_median'] * 100:+.1f}%)")
    if pd.notna(row.get("ret_20d")) and row["ret_20d"] > EXTENDED_20D:
        out.append(f"already {row['ret_20d'] * 100:+.0f}% in 20 sessions: late entries in extended moves give back more")
    if pd.notna(row.get("atr_pctile_250")) and row["atr_pctile_250"] >= 0.9:
        out.append("its daily range is expanding; that has preceded underperformance (IC -0.086)")
    if (breadth_state or "").lower() in ("narrow", "weak"):
        out.append(f"market breadth is {breadth_state}: few stocks are carrying the market")
    out.append("the ranking's measured edge is small: the best decile earned about +0.4% over 20 sessions, before costs")
    return out


def execution(row: pd.Series, alpha: dict) -> dict:
    daily = row["vol_60"] / np.sqrt(252.0)
    p = row["price"]
    return {"band": (p * (1 - 0.25 * daily), p * (1 + 0.25 * daily)), "dont_chase": p * (1 + daily),
            "slippage_bp": (alpha.get("spread", 0) + alpha.get("impact", 0)) / 2 * 1e4,
            "how": "limit order inside the band; under 10% of the day's volume; skip the open's first 15 minutes"}


def watched_not_traded(best: pd.DataFrame, shown: set[str], k: int = 5) -> list[dict]:
    """The names with the highest expected return before costs that did not make the list, and why."""
    rest = best[~best["ticker"].isin(shown)].sort_values("expected_return", ascending=False).head(k)
    out = []
    for r in rest.itertuples():
        a = r.alpha if isinstance(r.alpha, dict) else {}
        if r.expected_net < 0:
            why = (f"costs {a.get('costs', r.cost) * 100:.2f}% and risk penalty {a.get('risk_penalty', 0) * 100:.2f}% "
                   f"exceed the expected {r.expected_return * 100:+.2f}%")
        elif a.get("impact", 0) > a.get("fees", 0):
            why = "too thin: market impact is the largest cost"
        else:
            why = "ranked below the top 10 on net alpha"
        out.append({"ticker": r.ticker, "type": r.type, "expected_return": r.expected_return, "net": r.expected_net, "why": why})
    return out


CALC_JS = """
<script>
(function(){
  var rows = JSON.parse(document.getElementById('fb-sizing').textContent);
  function fmt(n){ return n.toLocaleString('en-IN', {maximumFractionDigits: 0}); }
  function run(){
    var cap = parseFloat(document.getElementById('fb-capital').value) || 0;
    var risk = (parseFloat(document.getElementById('fb-risk').value) || 0) / 100;
    var maxw = (parseFloat(document.getElementById('fb-maxw').value) || 10) / 100;
    rows.forEach(function(r){
      var perShare = r.price - r.stop;
      var byRisk = perShare > 0 ? cap * risk / perShare : 0;
      var shares = Math.floor(Math.min(byRisk, cap * maxw / r.price, 0.1 * r.adv / r.price));
      var el = document.getElementById('fb-size-' + r.id);
      if (el) el.textContent = shares > 0 ? shares + ' shares = Rs ' + fmt(shares * r.price) + ' (' + (shares * r.price / cap * 100).toFixed(1)
        + '% of capital), loses Rs ' + fmt(shares * perShare) + ' (' + (shares * perShare / cap * 100).toFixed(2) + '%) at the stop' : 'below one share at this risk';
    });
  }
  ['fb-capital','fb-risk','fb-maxw'].forEach(function(id){ document.getElementById(id).addEventListener('input', run); });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run); else run();
})();
</script>
"""


def calculator(rows: list[dict]) -> str:
    """Position sizing in the page: the trader's capital and risk, the stop the system set. Runs in the browser."""
    return ("<div class='tile' style='margin:10px 0'><div class='k'>Size every trade to your account</div><div style='display:flex;gap:12px;flex-wrap:wrap;margin-top:6px'>"
            "<label>Capital Rs <input id='fb-capital' type='number' value='500000' min='0' step='10000' style='width:120px'></label>"
            "<label>Risk per trade % <input id='fb-risk' type='number' value='1' min='0' max='5' step='0.25' style='width:70px'></label>"
            "<label>Max in one name % <input id='fb-maxw' type='number' value='10' min='1' max='100' step='1' style='width:70px'></label></div>"
            "<div class='sub'>Shares = capital x risk / (price - stop), capped by the weight limit and 10% of daily turnover. Information, not advice.</div></div>"
            f"<script type='application/json' id='fb-sizing'>{json.dumps(rows).replace('</', '<\\/')}</script>" + CALC_JS)


def card(r, row: pd.Series, comp: pd.Series, *, rank: int, universe: int, decile_mean, analogue, breadth_state, fno,
         returns, plan: dict, sid: str, fund: dict | None = None) -> str:
    """One opportunity, answered the way a trader asks: why this, why now, edge, if wrong, how much, how, bear case."""
    from ..decisions import net_alpha as NA
    a = r.alpha if isinstance(r.alpha, dict) else {}
    bucket = NA.bucket(float(row["adv20"]))
    out_ = outcome(returns, plan["stop_distance"]) if returns is not None else {}
    ex = execution(row, a)
    checks = "".join(f"<div>{'&#10003;' if ok else '&#9888;'} {_e(t)}</div>" for ok, t in why_now(row))
    panel = "".join(f"<tr><td>{_e(k)}</td><td>{_e(v)}</td><td style='white-space:normal'>{_e(d)}</td></tr>"
                    for k, v, d in edge_panel(row, decile_mean=decile_mean, analogue=analogue, breadth_state=breadth_state,
                                              fno=fno, bucket=bucket, ep_pct=None, fund=fund))
    items = [("Expected return", a.get("expected_return")), ("Benchmark", -a.get("benchmark", 0)), ("Fees and taxes", -a.get("fees", 0)),
             ("Spread", -a.get("spread", 0)), ("Market impact", -a.get("impact", 0)), ("Risk penalty", -a.get("risk_penalty", 0))]
    ledger_rows = "".join(f"<tr><td>{k}</td><td class='r'>{(v or 0) * 100:+.2f}%</td></tr>" for k, v in items)
    ledger_rows += f"<tr><td><b>Expected net alpha</b></td><td class='r'><b>{r.expected_net * 100:+.2f}%</b></td></tr>"
    wrong = (f"P(up) {out_['p_up']:.0%} · if right about {out_['gain_if_right'] * 100:+.1f}% · if wrong about -{out_['loss_if_wrong'] * 100:.1f}% "
             f"(stop caps it at -{plan['stop_distance'] * 100:.1f}%) · reward/risk {out_['reward_risk']:.2f} · range {out_['p10'] * 100:+.1f}% / "
             f"{out_['p50'] * 100:+.1f}% / {out_['p90'] * 100:+.1f}% (10th/median/90th; bootstrap of its own history, not conditioned on the signal)"
             if out_ else "-")
    bear = "".join(f"<li>{_e(b)}</li>" for b in bear_case(row, analogue, breadth_state))
    why = "".join(f"<li>{_e(w)}</li>" for w in why_this(row, comp, rank, universe))
    return (f"<details id='{_e(r.ticker)}'><summary>#{rank} {_e(r.ticker)} · {_e(r.type)} ({r.horizon} sessions) · net {r.expected_net * 100:+.2f}%</summary>"
            f"<div class='kv'><div>Why this</div><div><ul style='margin:0;padding-left:18px'>{why}</ul></div>"
            f"<div>Why now</div><div>{checks}</div>"
            f"<div>The edge</div><div><div class='wrap'><table>{panel}</table></div></div>"
            f"<div>Expected net alpha</div><div><div class='wrap' style='max-width:360px'><table>{ledger_rows}</table></div></div>"
            f"<div>If I'm wrong</div><div>{wrong}</div>"
            f"<div>Invalidation</div><div>a close below {plan['stop_price']:,.2f} ({plan['stop_distance']:.1%}); the composite falling into the bottom 20% at a review; "
            f"breadth turning weak; a results filing before the time exit ({plan['time_exit_on']})</div>"
            f"<div>How much</div><div id='fb-size-{sid}'>-</div>"
            f"<div>How to enter</div><div>buy between {ex['band'][0]:,.2f} and {ex['band'][1]:,.2f}; do not chase above {ex['dont_chase']:,.2f}; "
            f"expected slippage about {ex['slippage_bp']:.0f} bp a side; {_e(ex['how'])}</div>"
            f"<div>Case against</div><div><ul style='margin:0;padding-left:18px'>{bear}</ul></div>"
            f"<div>Exit</div><div>no profit target (targets added nothing in the exit study); time exit on {plan['time_exit_on']}, or the stop</div>"
            "</div></details>")
