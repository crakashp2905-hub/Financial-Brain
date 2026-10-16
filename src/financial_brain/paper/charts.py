"""A chart page per name: every indicator drawn, every strategy's buys and sells marked, every reading tabled.

One self-contained HTML file per ticker (``data/terminal/charts/TICKER.html``), drawn with TradingView's open-source
lightweight-charts loaded from a CDN:

    price pane     candles and volume; switchable overlays - moving averages 20/50/200, Bollinger bands, Ichimoku
                   (tenkan, kijun, the cloud), Supertrend, Parabolic SAR, the 20-session VWAP; horizontal levels for
                   the Fibonacci retracements of the 1-year swing, open fair value gaps, and today's pivots
    lower panes    RSI 14; MACD with its signal line and histogram
    markers        buys and sells of the strategy picked in the menu: the eleven technical rules (their tested
                   rules, frozen) and the paper books' actual entries and exits for this name
    table          every library feature's latest value, the direction it has been bullish in (2016-21), whether it
                   passed the deep-library test, the consensus vote - and the learned model's reasons when it holds it

Information, not advice: a chart shows what each rule says; the brain trades only what passed its tests.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("data") / "terminal" / "charts"
LWC = "https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"


def _t(d) -> str:
    return str(pd.Timestamp(d).date())


def overlays(b: pd.DataFrame) -> dict:
    """Overlay and pane series from adjusted daily bars (d, o, h, l, c, v), oldest first."""
    o, h, l, c, v = (b[k].astype(float).reset_index(drop=True) for k in ("o", "h", "l", "c", "v"))
    d = [_t(x) for x in b["d"]]
    sma = lambda n: c.rolling(n).mean()                                   # noqa: E731
    sd20 = c.rolling(20).std()
    mid = lambda n: (h.rolling(n).max() + l.rolling(n).min()) / 2         # noqa: E731
    tenkan, kijun = mid(9), mid(26)
    span_a, span_b = ((tenkan + kijun) / 2).shift(26), mid(52).shift(26)   # the cloud as published for each day
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(10).mean().to_numpy()
    st = np.full(len(c), np.nan)
    fu = fl = np.nan
    trend = 1
    C = c.to_numpy()
    hl2 = ((h + l) / 2).to_numpy()
    for t in range(1, len(C)):
        if not np.isfinite(atr[t]):
            continue
        bu, bl = hl2[t] + 3 * atr[t], hl2[t] - 3 * atr[t]
        fu = bu if (fu != fu or bu < fu or C[t - 1] > fu) else fu
        fl = bl if (fl != fl or bl > fl or C[t - 1] < fl) else fl
        trend = 1 if C[t] > fu else -1 if C[t] < fl else trend
        st[t] = fl if trend == 1 else fu
    H, L = h.to_numpy(), l.to_numpy()
    sar = np.full(len(C), np.nan)
    if len(C) > 2:
        up, s_, ep, af = True, L[0], H[0], 0.02
        for t in range(1, len(C)):
            s_ = s_ + af * (ep - s_)
            if up:
                s_ = min(s_, L[t - 1], L[max(t - 2, 0)])
                if L[t] < s_:
                    up, s_, ep, af = False, ep, L[t], 0.02
                elif H[t] > ep:
                    ep, af = H[t], min(af + 0.02, 0.2)
            else:
                s_ = max(s_, H[t - 1], H[max(t - 2, 0)])
                if H[t] > s_:
                    up, s_, ep, af = True, ep, H[t], 0.02
                elif L[t] < ep:
                    ep, af = L[t], min(af + 0.02, 0.2)
            sar[t] = s_
    tp = (h + l + c) / 3
    vwap = (tp * v).rolling(20).sum() / v.rolling(20).sum()
    dl = c.diff()
    rsi = 100 - 100 / (1 + dl.clip(lower=0).rolling(14).mean() / ((-dl.clip(upper=0)).rolling(14).mean() + 1e-12))
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    sig = macd.ewm(span=9, adjust=False).mean()

    def line(s):
        return [{"time": d[i], "value": round(float(x), 4)} for i, x in enumerate(np.asarray(s, float)) if np.isfinite(x)]
    return {"candles": [{"time": d[i], "open": float(o[i]), "high": float(h[i]), "low": float(l[i]), "close": float(c[i])} for i in range(len(d))],
            "volume": [{"time": d[i], "value": float(v[i]), "color": "#26a69a55" if c[i] >= o[i] else "#ef535055"} for i in range(len(d))],
            "sma20": line(sma(20)), "sma50": line(sma(50)), "sma200": line(sma(200)),
            "bb_up": line(sma(20) + 2 * sd20), "bb_lo": line(sma(20) - 2 * sd20),
            "tenkan": line(tenkan), "kijun": line(kijun), "span_a": line(span_a), "span_b": line(span_b),
            "supertrend": line(st), "psar": line(sar), "vwap20": line(vwap),
            "rsi": line(rsi), "macd": line(macd), "macd_signal": line(sig),
            "macd_hist": [{"time": d[i], "value": round(float(x), 4), "color": "#26a69a" if x >= 0 else "#ef5350"}
                          for i, x in enumerate((macd - sig).to_numpy()) if np.isfinite(x)]}


def levels(b: pd.DataFrame) -> list[dict]:
    """Horizontal levels as of the last bar: Fibonacci retracements of the 1-year swing, open fair value gaps (last 20
    sessions, not yet filled), and today's classic pivots."""
    h, l, c = (b[k].astype(float).to_numpy() for k in ("h", "l", "c"))
    out = []
    n = min(250, len(c))
    hi, lo = h[-n:].max(), l[-n:].min()
    up = np.argmax(h[-n:]) > np.argmin(l[-n:])
    for f in (0.236, 0.382, 0.5, 0.618, 0.786):
        px = hi - f * (hi - lo) if up else lo + f * (hi - lo)
        out.append({"price": round(float(px), 2), "title": f"Fib {f:.1%}", "color": "#b8860b"})
    gaps = []
    for t in range(max(2, len(c) - 20), len(c)):
        if l[t] > h[t - 2]:
            gaps.append(("bull", h[t - 2], l[t], t))
        if h[t] < l[t - 2]:
            gaps.append(("bear", h[t], l[t - 2], t))
    for side, bot, top, t in gaps:
        after_l, after_h = l[t + 1:], h[t + 1:]
        filled = (side == "bull" and len(after_l) and after_l.min() <= bot) or (side == "bear" and len(after_h) and after_h.max() >= top)
        if not filled:
            col = "#2e7d32" if side == "bull" else "#c62828"
            out += [{"price": round(float(top), 2), "title": f"FVG {side} top", "color": col},
                    {"price": round(float(bot), 2), "title": f"FVG {side} bottom", "color": col}]
    P = (h[-1] + l[-1] + c[-1]) / 3
    out += [{"price": round(float(P), 2), "title": "Pivot", "color": "#5c6bc0"},
            {"price": round(float(2 * P - l[-1]), 2), "title": "R1", "color": "#5c6bc0"},
            {"price": round(float(2 * P - h[-1]), 2), "title": "S1", "color": "#5c6bc0"}]
    return out


def markers(b: pd.DataFrame, extra: dict[str, list] | None = None, sf: pd.DataFrame | None = None) -> dict[str, list]:
    """Buy and sell markers per strategy: the eleven technical rules' position changes, any book's trades, and - with
    ``sf`` (paper/signals.signal_frame) - every indicator's firings."""
    from ..evaluation import rule_systems as RS
    d = [_t(x) for x in b["d"]]
    out = {}
    for name, pos in RS.rules(b.reset_index(drop=True)).items():
        p = pos.to_numpy()
        ch = np.diff(np.concatenate([[0], p]))
        out[name] = [{"time": d[i], "position": "belowBar" if ch[i] > 0 else "aboveBar", "color": "#2e7d32" if ch[i] > 0 else "#c62828",
                      "shape": "arrowUp" if ch[i] > 0 else "arrowDown", "text": "buy" if ch[i] > 0 else "sell"}
                     for i in np.flatnonzero(ch) if i >= len(d) - 260]
    if sf is not None:
        from . import signals as SG
        for k in sf.columns:
            if k.startswith("algo:"):
                continue
            x = sf[k].to_numpy()
            ms = []
            for i in np.flatnonzero(x):
                if i < len(d) - 260:
                    continue
                side = SG.side_of(k, x[i])
                up = side != "sell"
                ms.append({"time": d[i], "position": "belowBar" if up else "aboveBar",
                           "color": "#2e7d32" if side == "buy" else "#c62828" if side == "sell" else "#8d6e00",
                           "shape": "arrowUp" if side == "buy" else "arrowDown" if side == "sell" else "circle", "text": side})
            if ms:
                out[f"indicator: {SG.label(k)}"] = ms
    for k, v in (extra or {}).items():
        out[k] = v
    return out


def feature_table(latest: pd.Series, directions: pd.Series | None, sweep: pd.DataFrame | None,
                  pct: pd.Series | None = None) -> list[dict]:
    rows = []
    for f, val in latest.items():
        if not isinstance(val, (int, float, np.floating)) or not np.isfinite(val):
            continue
        dirn = None if directions is None else directions.get(f)
        ev = "-"
        if sweep is not None and len(sweep):
            s = sweep[sweep["feature"] == f]
            ev = "passed (FDR, tiers, held-out)" if s["counts"].any() else "FDR only" if s["fdr"].any() else "not significant"
        rows.append({"feature": f, "value": round(float(val), 4), "percentile": None if pct is None or f not in pct else round(float(pct[f]), 2),
                     "bullish_when": "high" if dirn and dirn > 0 else "low" if dirn and dirn < 0 else "-", "evidence": ev})
    return rows


PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(ticker)s - Financial-Brain chart</title>
<style>
:root{--bg:#f6f7f4;--fg:#1d2321;--muted:#5f6b66;--line:#d9ded9;--card:#fff}
@media (prefers-color-scheme: dark){:root{--bg:#121615;--fg:#e4e8e6;--muted:#93a09a;--line:#2a312e;--card:#1a201e}}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,Segoe UI,sans-serif}
main{max-width:1200px;margin:0 auto;padding:16px}h1{font-size:20px;margin:0}.sub{color:var(--muted);font-size:13px}
.ctl{display:flex;flex-wrap:wrap;gap:10px;margin:10px 0}.ctl label{font-size:13px}
.pane{border:1px solid var(--line);background:var(--card);border-radius:6px;margin:6px 0}
.wrap{overflow-x:auto;border:1px solid var(--line);border-radius:6px;background:var(--card);max-height:420px}
table{border-collapse:collapse;width:100%%;font-variant-numeric:tabular-nums}td,th{padding:5px 8px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{position:sticky;top:0;background:var(--card);color:var(--muted);font-size:12px}
</style></head><body><main>
<h1>%(ticker)s</h1><div class="sub">%(subtitle)s</div>
<div class="ctl" id="ctl"></div>
<div class="ctl"><label>Buy/sell signals of <select id="sig"></select></label></div>
<div id="price" class="pane" style="height:460px"></div><div id="rsi" class="pane" style="height:120px"></div><div id="macd" class="pane" style="height:140px"></div>
<h3>Firing on this stock now</h3><div class="sub">%(fired)s</div>
<h3>Why the brain holds it</h3><div class="sub">%(why)s</div>
<h3>Every indicator, today</h3><div class="sub">%(consensus)s</div>
<div class="wrap"><table><tr><th>Feature</th><th>Value</th><th>Percentile vs the 500</th><th>Bullish when</th><th>Evidence</th></tr>%(rows)s</table></div>
<p class="sub">Information, not advice. A marker shows what a rule says; the brain trades only what passed its tests (see the research notes).</p>
</main>
<script src="%(lwc)s"></script>
<script>
const D = %(data)s;
const dark = matchMedia('(prefers-color-scheme: dark)').matches;
const opts = el => ({width: el.clientWidth, height: el.clientHeight, layout:{background:{color:'transparent'}, textColor: dark?'#e4e8e6':'#1d2321'},
  grid:{vertLines:{color: dark?'#2a312e':'#eceeea'}, horzLines:{color: dark?'#2a312e':'#eceeea'}}, timeScale:{borderVisible:false}, rightPriceScale:{borderVisible:false}});
const pc = LightweightCharts.createChart(document.getElementById('price'), opts(document.getElementById('price')));
const candles = pc.addCandlestickSeries(); candles.setData(D.s.candles);
const vol = pc.addHistogramSeries({priceScaleId:'', priceFormat:{type:'volume'}}); vol.priceScale().applyOptions({scaleMargins:{top:0.8,bottom:0}}); vol.setData(D.s.volume);
const OV = {sma20:['#1e88e5',1], sma50:['#fb8c00',1], sma200:['#8e24aa',2], bb_up:['#90a4ae',1], bb_lo:['#90a4ae',1], tenkan:['#00897b',1], kijun:['#d81b60',1],
  span_a:['#66bb6a',1], span_b:['#ef5350',1], supertrend:['#3949ab',2], psar:['#6d4c41',1], vwap20:['#f9a825',1]};
const NAMES = {sma20:'MA 20', sma50:'MA 50', sma200:'MA 200', bb_up:'Bollinger', tenkan:'Ichimoku', supertrend:'Supertrend', psar:'Parabolic SAR', vwap20:'VWAP 20'};
const GROUP = {bb_up:['bb_up','bb_lo'], tenkan:['tenkan','kijun','span_a','span_b']};
const series = {};
for (const [k,[col,w]] of Object.entries(OV)) { series[k] = pc.addLineSeries({color:col, lineWidth:w, priceLineVisible:false, lastValueVisible:false,
  lineStyle: k==='psar'?3:0, visible: ['sma50','sma200','supertrend'].includes(k)}); series[k].setData(D.s[k]); }
const ctl = document.getElementById('ctl');
for (const [k,label] of Object.entries(NAMES)) { const id='o_'+k; const box=document.createElement('label');
  box.innerHTML = `<input type=checkbox id=${id} ${['sma50','sma200','supertrend'].includes(k)?'checked':''}> ${label}`; ctl.appendChild(box);
  document.getElementById(id).addEventListener('change', e => (GROUP[k]||[k]).forEach(s => series[s].applyOptions({visible:e.target.checked}))); }
const lv = document.createElement('label'); lv.innerHTML = '<input type=checkbox id=o_levels checked> Fibonacci, FVG and pivot levels'; ctl.appendChild(lv);
let lines = D.levels.map(x => candles.createPriceLine({price:x.price, color:x.color, lineWidth:1, lineStyle:2, axisLabelVisible:true, title:x.title}));
document.getElementById('o_levels').addEventListener('change', e => { if (e.target.checked) lines = D.levels.map(x => candles.createPriceLine({price:x.price,
  color:x.color, lineWidth:1, lineStyle:2, axisLabelVisible:true, title:x.title})); else { lines.forEach(l => candles.removePriceLine(l)); lines = []; } });
const sel = document.getElementById('sig');
for (const k of Object.keys(D.markers)) { const o=document.createElement('option'); o.value=k; o.textContent=k + ' (' + D.markers[k].length + ')'; sel.appendChild(o); }
const show = k => candles.setMarkers((D.markers[k]||[]).slice().sort((a,b)=>a.time<b.time?-1:1));
sel.addEventListener('change', e => show(e.target.value)); if (sel.options.length) { sel.value = D.default_signal || sel.options[0].value; show(sel.value); }
const rc = LightweightCharts.createChart(document.getElementById('rsi'), opts(document.getElementById('rsi')));
const rs = rc.addLineSeries({color:'#7e57c2', lineWidth:1}); rs.setData(D.s.rsi);
[30,70].forEach(p => rs.createPriceLine({price:p, color:'#9e9e9e', lineWidth:1, lineStyle:2}));
const mc = LightweightCharts.createChart(document.getElementById('macd'), opts(document.getElementById('macd')));
mc.addHistogramSeries().setData(D.s.macd_hist); mc.addLineSeries({color:'#1e88e5', lineWidth:1}).setData(D.s.macd); mc.addLineSeries({color:'#fb8c00', lineWidth:1}).setData(D.s.macd_signal);
const charts = [pc, rc, mc];
charts.forEach(c => c.timeScale().subscribeVisibleLogicalRangeChange(r => { if (r) charts.forEach(o => o !== c && o.timeScale().setVisibleLogicalRange(r)); }));
pc.timeScale().setVisibleLogicalRange({from: D.s.candles.length - 260, to: D.s.candles.length});
addEventListener('resize', () => charts.forEach((c,i) => { const el=[ 'price','rsi','macd'][i]; c.applyOptions({width: document.getElementById(el).clientWidth}); }));
</script></body></html>"""


def page(ticker: str, b: pd.DataFrame, *, subtitle: str = "", why: str = "", table: list[dict] | None = None,
         extra_markers: dict | None = None, default_signal: str | None = None, consensus: str = "",
         sf: pd.DataFrame | None = None, fired: list[str] | None = None) -> str:
    data = {"s": overlays(b), "levels": levels(b), "markers": markers(b, extra_markers, sf), "default_signal": default_signal}
    rows = "".join(f"<tr><td>{html.escape(r['feature'])}</td><td>{r['value']}</td><td>{'-' if r['percentile'] is None else r['percentile']}</td>"
                   f"<td>{r['bullish_when']}</td><td>{html.escape(r['evidence'])}</td></tr>" for r in (table or []))
    return PAGE % {"ticker": html.escape(ticker), "subtitle": html.escape(subtitle), "why": html.escape(why or "-"),
                   "consensus": html.escape(consensus), "rows": rows,
                   "fired": html.escape("; ".join(fired)) if fired else "nothing in the last 5 sessions (see the signal screener)", "lwc": LWC,
                   "data": json.dumps(data).replace("</", "<\\/")}


def build_many(rcon, tickers: list[str], asof, *, book=None, out: Path = OUT, fired: dict[str, list[str]] | None = None,
               frames: dict[str, tuple] | None = None) -> dict[str, Path]:
    """Chart pages for ``tickers`` as of ``asof``. ``frames``: ticker -> (bars, features, signal frame) already
    computed by paper/signals; ``fired``: ticker -> what fired lately. Returns ticker -> path."""
    from ..evaluation import rule_systems as RS
    from . import signals as SG
    from ..evaluation import learned_model as LM
    from ..features import library as LB
    from ..features import technical as TE
    out.mkdir(parents=True, exist_ok=True)
    tickers = sorted({t for t in tickers if t})
    if not tickers:
        return {}
    lin = dict(rcon.execute("SELECT ticker, lineage FROM adjusted_prices WHERE business_date = ? AND ticker IN (SELECT UNNEST(?))",
                            [asof, tickers]).fetchall())
    rcon.execute(TE.BARS_SQL)
    bars = rcon.execute("""SELECT lineage, d, o, h, l, c, v FROM _tbars WHERE lineage IN (SELECT UNNEST(?)) AND d <= ?
                           AND d > ?::DATE - INTERVAL 800 DAY ORDER BY lineage, d""", [list(lin.values()), asof, asof]).df()
    bars["d"] = pd.to_datetime(bars["d"]).dt.date
    try:
        directions = pd.read_parquet("data/consensus_directions.parquet")["direction"]
    except Exception:                                   # noqa: BLE001
        directions = None
    try:
        sweep = pd.read_parquet("data/feature_library_sweep.parquet")
    except Exception:                                   # noqa: BLE001
        sweep = None
    try:
        scored = LM.score_day(rcon, asof)
    except Exception:                                   # noqa: BLE001
        scored = None
    trades = {}
    if book is not None:
        for v, tk, ed, ep, xd, xp in book.execute("""SELECT version, ticker, entry_day, entry_px, exit_day, exit_px FROM positions
                                                     WHERE mode = 'live' AND ticker IN (SELECT UNNEST(?))""", [tickers]).fetchall():  # noqa: B007
            m = trades.setdefault(tk, {}).setdefault(f"book: {v}", [])
            if ed:
                m.append({"time": _t(ed), "position": "belowBar", "color": "#2e7d32", "shape": "arrowUp", "text": f"{v} buy"})
            if xd:
                m.append({"time": _t(xd), "position": "aboveBar", "color": "#c62828", "shape": "arrowDown", "text": f"{v} sell"})
    paths = {}
    frames = frames or {}
    for tk in tickers:
        if tk in frames:
            g, f, sf = frames[tk]
        else:
            g = bars[bars["lineage"] == lin.get(tk)].reset_index(drop=True)
            if len(g) < 220:
                continue
            f = LB.compute(g)
            sf = SG.signal_frame(f, RS.rules(g))
        latest = f[LB.feature_names(f)].iloc[-1]
        why, pct, cons = "", None, ""
        if scored is not None and lin.get(tk) in set(scored["lineage"]):
            row = scored[scored["lineage"] == lin[tk]].iloc[0]
            why = f"learned-model score {row['pred']:.3f} (rank {int((scored['pred'] > row['pred']).sum()) + 1} of {len(scored)}): {row.get('why', '')}"
            feats = [c for c in scored.columns if c in latest.index]
            if feats:
                pct = scored[feats].rank(pct=True)[scored["lineage"] == lin[tk]].iloc[0]
        if directions is not None:
            common = [x for x in latest.index if x in directions.index and np.isfinite(latest[x])]
            if pct is not None:
                votes = [(pct[x] - 0.5) * directions[x] for x in common if x in pct.index and np.isfinite(pct[x])]
                bull = sum(1 for x in votes if x > 0)
                cons = (f"Consensus vote: {bull} of {len(votes)} indicators lean bullish for this name (the vote failed its test as a "
                        "way to pick trades, -0.38%/month 2022-26; shown for information).")
        table = feature_table(latest, directions, sweep, pct)
        sub = f"Data through {asof}. Overlays and levels as known at each close; markers show each rule's own buys and sells."
        p = out / f"{tk.replace('&', '_')}.html"
        p.write_text(page(tk, g, subtitle=sub, why=why, table=table, extra_markers=trades.get(tk),
                          default_signal=next(iter(trades.get(tk, {})), "ma_cross_50_200"), consensus=cons,
                          sf=sf, fired=(fired or {}).get(tk)), encoding="utf-8")
        paths[tk] = p
    return paths
