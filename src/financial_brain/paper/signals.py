"""The signal screener: for every algorithm and indicator, the stocks it is firing on now - each linked to its chart.

Written to ``data/terminal/signals.html`` beside the terminal. For the 500 most liquid names, over the last few
sessions:

    algorithms     the eleven technical rules (frozen as tested): fresh buys, fresh sells, and how many hold now
    indicators     events from the deep feature library: fair value gaps (new, retested, weekly), liquidity sweeps,
                   breaks of structure, unfilled gaps, candlestick patterns, range contraction, Fibonacci golden
                   pocket entries, and flips of Supertrend, Parabolic SAR, Heikin-Ashi, Ichimoku and moving-average
                   crosses
    strategies     what the paper books and the zoo hold now

Every signal carries its tested evidence beside it - the rule's 2022-26 net Sharpe, or the indicator's 20-session
rank IC (t, and the held-out t) from the deep-library sweep - because most of them did not beat costs. Information,
not advice: a listed stock is where a signal fired, not a recommendation.
"""
from __future__ import annotations

import html
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("data") / "terminal" / "signals.html"
WINDOW = 5                 #: sessions back a signal counts as "firing now"
MIN_VOL = 0.003            #: daily volatility below this is a liquid fund or cash ETF, not a stock

#: binary library events (1 = fired that session): name -> (label, side)
EVENTS = {
    "fvg_bull_new": ("Fair value gap, bullish (new)", "buy"), "fvg_bull_retest": ("Fair value gap, bullish retest", "buy"),
    "fvg_w_bull_new": ("Weekly fair value gap, bullish", "buy"), "fvg_bear_new": ("Fair value gap, bearish (new)", "sell"),
    "fvg_bear_retest": ("Fair value gap, bearish retest", "sell"), "fvg_w_bear_new": ("Weekly fair value gap, bearish", "sell"),
    "sweep_bull": ("Liquidity sweep of the lows", "buy"), "sweep_bear": ("Liquidity sweep of the highs", "sell"),
    "bos_up": ("Break of structure, up", "buy"), "bos_down": ("Break of structure, down", "sell"),
    "gap_up_unfilled": ("Gap up, unfilled", "buy"), "gap_down_unfilled": ("Gap down, unfilled", "sell"),
    "c_bull_engulf": ("Bullish engulfing", "buy"), "c_bull_harami": ("Bullish harami", "buy"), "c_hammer": ("Hammer", "buy"),
    "c_morning_star": ("Morning star", "buy"), "c_piercing": ("Piercing line", "buy"), "c_three_soldiers": ("Three white soldiers", "buy"),
    "c_bear_engulf": ("Bearish engulfing", "sell"), "c_dark_cloud": ("Dark cloud cover", "sell"), "c_evening_star": ("Evening star", "sell"),
    "c_shooting_star": ("Shooting star", "sell"), "c_three_crows": ("Three black crows", "sell"),
    "c_doji": ("Doji", "watch"), "inside_bar": ("Inside bar", "watch"), "outside_bar": ("Outside bar", "watch"),
    "nr4": ("Narrowest range in 4 (NR4)", "watch"), "nr7": ("Narrowest range in 7 (NR7)", "watch"),
    "wide_range": ("Wide-range bar (> 2 ATR)", "watch"), "wyckoff_compression": ("Wyckoff compression", "watch"),
    "gartley": ("Gartley pattern", "watch"),
}
#: states whose change is the signal: name -> label; a move up is a buy, a move down a sell
STATES = {
    "supertrend_dir": "Supertrend flip", "psar_dir": "Parabolic SAR flip", "ha_color": "Heikin-Ashi colour change",
    "ichi_tk_cross": "Ichimoku tenkan/kijun cross", "ichi_cloud_pos": "Ichimoku cloud break",
    "cross_20_50": "MA 20/50 cross", "cross_50_200": "Golden / death cross (MA 50/200)",
}
#: signed events (+1 buy side, -1 sell side, on the session the value turns non-zero)
SIGNED = {"c_marubozu": "Marubozu", "fib_golden_pocket_60": "Fibonacci golden pocket (60-session swing)",
          "fib_golden_pocket_250": "Fibonacci golden pocket (1-year swing)"}
RULE_LABELS = {"ma_cross_50_200": "MA 50/200 trend", "ema_ribbon": "EMA ribbon", "macd_cross": "MACD cross",
               "bollinger_reversion": "Bollinger reversion", "rsi_exhaustion": "RSI exhaustion", "stochastic_cross": "Stochastic cross",
               "atr_breakout": "ATR breakout", "ichimoku_breakout": "Ichimoku breakout", "donchian_55_20": "Donchian 55/20 breakout",
               "cci_trend": "CCI trend", "volume_spike_entry": "Volume spike entry"}


def signal_frame(f: pd.DataFrame, rules: dict[str, pd.Series] | None = None) -> pd.DataFrame:
    """One column per signal, aligned with ``f``: +1 a buy-side firing, -1 a sell-side one, 0 nothing.
    ``watch`` events fire as +1 (no side). Rule columns are prefixed ``algo:``."""
    cols = {}
    for k, (_, side) in EVENTS.items():
        if k in f:
            x = (f[k].fillna(0).to_numpy() > 0).astype(float)
            cols[k] = -x if side == "sell" else x
    for k in STATES:
        if k in f:
            s = np.sign(f[k].fillna(0).to_numpy())
            ch = np.diff(np.concatenate([[s[0] if len(s) else 0], s]))
            cols[k] = np.sign(ch)
    for k in SIGNED:
        if k in f:
            s = np.sign(f[k].fillna(0).to_numpy())
            prev = np.concatenate([[0], s[:-1]])
            cols[k] = np.where((s != 0) & (prev != s), s, 0.0)
    for k, pos in (rules or {}).items():
        p = pos.fillna(0).to_numpy()
        cols[f"algo:{k}"] = np.sign(np.diff(np.concatenate([[p[0] if len(p) else 0], p])))
    return pd.DataFrame(cols, index=f.index)


def label(sig: str) -> str:
    if sig.startswith("algo:"):
        return RULE_LABELS.get(sig[5:], sig[5:])
    return EVENTS.get(sig, (STATES.get(sig) or SIGNED.get(sig, sig),))[0]


def side_of(sig: str, value: float) -> str:
    if sig in EVENTS and EVENTS[sig][1] == "watch":
        return "watch"
    return "buy" if value > 0 else "sell"


def evidence() -> dict[str, str]:
    """Tested evidence per signal: rules from the Sharpe panel (2022-26), indicators from the deep-library sweep."""
    out = {}
    try:
        from ..evaluation import performance as PF
        P = pd.read_parquet("data/strategy_monthly_panel.parquet")
        P = P[P.index >= pd.Period("2022-01", "M")]
        for r in RULE_LABELS:
            if f"rule: {r}" in P:
                st = PF.stats(P[f"rule: {r}"].dropna())
                out[f"algo:{r}"] = f"net Sharpe 2022-26 {st['sharpe']:+.2f}, {st['annual_return']:+.1%} a year"
    except Exception:                                   # noqa: BLE001 - shown without evidence
        pass
    try:
        s = pd.read_parquet("data/feature_library_sweep.parquet")
        s = s[s["h"] == 20].set_index("feature")
        for k in [*EVENTS, *STATES, *SIGNED]:
            if k in s.index:
                r = s.loc[k]
                verdict = ("passed every test" if r["counts"] else "significant, not robust" if r["fdr"] else "no tested edge")
                out[k] = f"20-session IC t {r['t']:+.1f} (held-out {r['held_out_t']:+.1f}): {verdict}"
    except Exception:                                   # noqa: BLE001
        pass
    return out


def scan(bars: pd.DataFrame, *, window: int = WINDOW) -> tuple[pd.DataFrame, dict[str, tuple]]:
    """``bars``: lineage, ticker, d, o, h, l, c, v for many names (oldest first). Returns the firings in the last
    ``window`` sessions (signal, ticker, lineage, d, ago, side, close) and per-ticker (bars, features, signal frame)."""
    from ..evaluation import rule_systems as RS
    from ..features import library as LB
    rows, frames = [], {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for (lin, tk), g in bars.groupby(["lineage", "ticker"]):
            g = g.sort_values("d").reset_index(drop=True)
            if len(g) < 220 or g["c"].pct_change().tail(60).std() < MIN_VOL:
                continue                                # too short, or a liquid fund / ETF that barely moves
            f = LB.compute(g[["d", "o", "h", "l", "c", "v"]])
            sf = signal_frame(f, RS.rules(g))
            frames[tk] = (g, f, sf)
            tail = sf.iloc[-window:]
            n = len(g)
            for sig in tail.columns:
                for i in np.flatnonzero(tail[sig].to_numpy()):
                    j = n - len(tail) + i
                    rows.append({"signal": sig, "ticker": tk, "lineage": lin, "d": g["d"].iloc[j], "ago": n - 1 - j,
                                 "side": side_of(sig, tail[sig].iloc[i]), "close": float(g["c"].iloc[j])})
            for r, pos in RS.rules(g).items():
                if pos.iloc[-1] == 1:
                    rows.append({"signal": f"algo:{r}", "ticker": tk, "lineage": lin, "d": g["d"].iloc[-1], "ago": -1,
                                 "side": "holding", "close": float(g["c"].iloc[-1])})
    return pd.DataFrame(rows), frames


def _chart(t: str) -> str:
    return f"<a href='charts/{html.escape(t.replace('&', '_'))}.html'>{html.escape(t)}</a>"


PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Signal screener</title>
<style>
:root{--bg:#f5f6f2;--fg:#1c2220;--muted:#5d6964;--line:#d8ddd6;--card:#fff;--buy:#2e7d32;--sell:#c62828;--watch:#6d5d1e;--chip:#eef1ec}
@media (prefers-color-scheme: dark){:root{--bg:#121614;--fg:#e3e8e5;--muted:#93a09a;--line:#2a312d;--card:#1a1f1d;--buy:#7bc47f;--sell:#ef8a80;--watch:#d6c27a;--chip:#232a27}}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,Segoe UI,sans-serif}
main{max-width:1200px;margin:0 auto;padding-inline:16px;padding-block:16px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:22px 0 6px}
.sub{color:var(--muted);font-size:13px}.wrap{overflow-x:auto;border:1px solid var(--line);border-radius:6px;background:var(--card)}
table{border-collapse:collapse;width:100%%;font-variant-numeric:tabular-nums}td,th{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--muted);font-size:12px;font-weight:600;letter-spacing:.02em}td.r,th.r{text-align:right}
a{color:inherit}.tk a{display:inline-block;margin:1px 3px 1px 0;padding:1px 6px;border-radius:4px;background:var(--chip);text-decoration:none;font-size:12.5px}
.buy{color:var(--buy)}.sell{color:var(--sell)}.watch{color:var(--watch)}.ev{color:var(--muted);font-size:12.5px;min-width:220px}
nav{display:flex;flex-wrap:wrap;gap:12px;margin:8px 0}nav a{font-size:13px}
</style></head><body><main>
<h1>Signal screener</h1><div class="sub">%(sub)s</div>
<nav><a href="index.html">&larr; terminal</a><a href="#algos">Algorithms</a><a href="#ind">Indicators</a><a href="#stocks">By stock</a><a href="#strat">Strategies holding now</a></nav>
%(body)s
<p class="sub">Information, not advice. A stock is listed because a signal fired on it, not because the signal works: read the
evidence column - most of these did not beat costs when tested. Each name links to its chart, where every signal's
history is marked.</p>
</main></body></html>"""


def _signal_table(hits: pd.DataFrame, sigs: list[str], ev: dict, *, holding: bool) -> str:
    rows = []
    for sig in sigs:
        h = hits[hits["signal"] == sig]
        cell = {}
        for side in ("buy", "sell", "watch"):
            x = h[h["side"] == side].sort_values(["ago", "ticker"])
            cell[side] = " ".join(f"<span class='tk'>{_chart(t)}</span><span class='sub'>{'' if a == 0 else f'{a}d'}</span>"
                                  for t, a in zip(x["ticker"], x["ago"]))
        n_hold = int((h["side"] == "holding").sum())
        if not (cell["buy"] or cell["sell"] or cell["watch"] or n_hold):
            continue
        fire = cell["buy"] + (cell["watch"] if not cell["buy"] else "")
        rows.append(f"<tr><td><b>{html.escape(label(sig))}</b><div class='ev'>{html.escape(ev.get(sig, 'not tested on its own'))}</div></td>"
                    f"<td class='buy'>{fire or '-'}</td><td class='sell'>{cell['sell'] or '-'}</td>"
                    + (f"<td class='r'>{n_hold}</td>" if holding else "") + "</tr>")
    head = ("<tr><th>Signal and its tested evidence</th><th>Fired buy-side (sessions ago)</th><th>Fired sell-side</th>"
            + ("<th class='r'>Holding now</th>" if holding else "") + "</tr>")
    return "<div class='wrap'><table>" + head + "".join(rows) + "</table></div>"


def render(hits: pd.DataFrame, asof, *, strategies: dict[str, list[str]] | None = None, n_names: int = 0) -> str:
    ev = evidence()
    body = []
    algos = [f"algo:{r}" for r in RULE_LABELS]
    inds = [*EVENTS, *STATES, *SIGNED]
    body.append(f"<h2 id='algos'>Algorithms - the eleven technical rules</h2><div class='sub'>Fresh entries and exits in the last {WINDOW} "
                "sessions, and how many of the names each rule is long now.</div>")
    body.append(_signal_table(hits, algos, ev, holding=True) if len(hits) else "<div class='sub'>no data</div>")
    body.append(f"<h2 id='ind'>Indicators - events in the last {WINDOW} sessions</h2>")
    body.append(_signal_table(hits, inds, ev, holding=False) if len(hits) else "<div class='sub'>no data</div>")
    fresh = hits[hits["side"].isin(["buy", "sell"])] if len(hits) else hits
    if len(fresh):
        g = fresh.groupby("ticker")
        t = pd.DataFrame({"buy": g.apply(lambda x: (x["side"] == "buy").sum()), "sell": g.apply(lambda x: (x["side"] == "sell").sum()),
                          "which": g.apply(lambda x: ", ".join(sorted({('+' if s == 'buy' else '-') + label(k) for k, s in zip(x['signal'], x['side'])})))})
        t["net"] = t["buy"] - t["sell"]
        t = t.sort_values(["net", "buy"], ascending=False)
        top = pd.concat([t.head(25), t.tail(25)]).drop_duplicates()
        body.append("<h2 id='stocks'>By stock - most buy-side and most sell-side signals</h2><div class='sub'>Counts are not a vote "
                    "that works: the indicator consensus vote failed its test (-0.38%/month, 2022-26).</div><div class='wrap'><table>"
                    "<tr><th>Stock</th><th class='r'>Buy-side</th><th class='r'>Sell-side</th><th>Signals</th></tr>"
                    + "".join(f"<tr><td class='tk'>{_chart(k)}</td><td class='r buy'>{int(r['buy'])}</td><td class='r sell'>{int(r['sell'])}</td>"
                              f"<td class='sub'>{html.escape(r['which'])}</td></tr>" for k, r in top.iterrows()) + "</table></div>")
    if strategies:
        body.append("<h2 id='strat'>Strategies holding now</h2><div class='wrap'><table><tr><th>Strategy</th><th>Names</th></tr>"
                    + "".join(f"<tr><td><b>{html.escape(k)}</b></td><td>{' '.join(f'<span class=tk>{_chart(t)}</span>' for t in v) or '-'}</td></tr>"
                              for k, v in strategies.items()) + "</table></div>")
    sub = (f"The {n_names} most liquid names, data through {asof}. Click a name for its chart: price, every overlay, and "
           "each signal's buys and sells.")
    return PAGE % {"sub": html.escape(sub), "body": "".join(body)}


def strategies_now(rcon, asof, *, book=None, zoo=None) -> dict[str, list[str]]:
    """What each paper book and zoo strategy holds now, as tickers."""
    out = {}
    if book is not None:
        for v, tks in book.execute("""SELECT version, LIST(DISTINCT ticker ORDER BY ticker) FROM positions WHERE mode = 'live'
                                      AND exit_day IS NULL GROUP BY 1 ORDER BY 1""").fetchall():
            out[f"book: {v}"] = list(tks)
    if zoo is not None:
        last = zoo.execute("SELECT MAX(day) FROM zoo_targets").fetchone()[0]
        if last is not None:
            tg = zoo.execute("SELECT strategy, key, weight FROM zoo_targets WHERE day = ? AND strategy <> 'sharpe_allocator'", [last]).df()
            names = dict(rcon.execute("SELECT lineage, ticker FROM adjusted_prices WHERE business_date = ?", [asof]).fetchall())
            for s, g in tg.groupby("strategy"):
                tks = sorted({names.get(k, k.replace("CARRY:", "")) for k in g["key"] if k != "NIFTY50"})
                if tks:
                    out[f"zoo: {s}"] = tks[:60]
    return out


def build(rcon, asof, *, book=None, zoo=None, n: int = 500, out: Path = OUT, charts: bool = True) -> dict:
    """Scan the ``n`` most liquid names, write the screener, and a chart page for every name it lists."""
    from ..features import technical as TE
    from . import charts as CH
    lins = [r[0] for r in rcon.execute("""SELECT lineage FROM features WHERE business_date = ? AND adv20 > 0
                                          ORDER BY adv20 DESC LIMIT ?""", [asof, n]).fetchall()]
    rcon.execute(TE.BARS_SQL)
    bars = rcon.execute("""SELECT b.lineage, a.ticker, b.d, b.o, b.h, b.l, b.c, b.v FROM _tbars b
                           JOIN (SELECT lineage, ticker FROM adjusted_prices WHERE business_date = ?) a USING (lineage)
                           WHERE b.lineage IN (SELECT UNNEST(?)) AND b.d <= ? AND b.d > ?::DATE - INTERVAL 800 DAY
                           ORDER BY b.lineage, b.d""", [asof, lins, asof, asof]).df()
    bars["d"] = pd.to_datetime(bars["d"]).dt.date
    hits, frames = scan(bars)
    strat = strategies_now(rcon, asof, book=book, zoo=zoo)
    strat = {k: [t for t in v if t in frames] if k.startswith("zoo:") else v for k, v in strat.items()}   # funds out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(hits, asof, strategies=strat, n_names=len(frames)), encoding="utf-8")
    pages = {}
    if charts and len(hits):
        names = sorted(set(hits.loc[hits["side"] != "holding", "ticker"]) | {t for v in strat.values() for t in v})
        fired = {t: [f"{label(s)} ({'buy' if v == 'buy' else v}, {d})" for s, v, d in zip(g["signal"], g["side"], g["d"]) if v != "holding"]
                 for t, g in hits.groupby("ticker")}
        pages = CH.build_many(rcon, names, asof, book=book, out=out.parent / "charts", fired=fired, frames=frames)
    return {"names": len(frames), "firings": int((hits["side"] != "holding").sum()) if len(hits) else 0, "charts": len(pages)}
