"""The trader's first screen, as one static HTML page written each day: market, top opportunities, active paper
trades, what changed, risk - and, for each top name, why now, its analogues, its range, its net alpha, its size,
its stop and how it would be executed.

Built only from what the system has recorded or can compute from its own data. Every number on it carries the
evidence label it came with; nothing is presented as a recommendation, and filing text (untrusted: a company typed
it) is HTML-escaped and shown as data.
"""
from __future__ import annotations

import html
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("data") / "terminal" / "index.html"
INDICES = ("Nifty 50", "Nifty 500", "Nifty Bank", "India VIX")

CSS = """
:root{--bg:#f6f7f4;--fg:#1d2321;--muted:#5f6b66;--line:#d9ded9;--card:#ffffff;--pos:#1f7a4d;--neg:#b23b2e;--accent:#2d5d7b;--warn:#9a6b00}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#121615;--fg:#e4e8e6;--muted:#93a09a;--line:#2a312e;--card:#1a201e;--pos:#5cc08b;--neg:#e2786a;--accent:#7fb2d4;--warn:#e0b04a}}
:root[data-theme="dark"]{--bg:#121615;--fg:#e4e8e6;--muted:#93a09a;--line:#2a312e;--card:#1a201e;--pos:#5cc08b;--neg:#e2786a;--accent:#7fb2d4;--warn:#e0b04a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 "IBM Plex Sans",system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1180px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:20px;margin:0 0 2px;letter-spacing:.01em}h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:26px 0 8px}
h3{font-size:16px;margin:0 0 6px}.sub{color:var(--muted);font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:10px 12px}
.tile .k{color:var(--muted);font-size:12px}.tile .v{font-size:18px;font-variant-numeric:tabular-nums}
.wrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:6px}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}th,td{padding:7px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}
th{font-size:12px;color:var(--muted);font-weight:600}td.r,th.r{text-align:right}tr:last-child td{border-bottom:none}
.pos{color:var(--pos)}.neg{color:var(--neg)}.chip{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px;border:1px solid var(--line);color:var(--muted)}
.chip.warn{border-color:var(--warn);color:var(--warn)}.chip.ok{border-color:var(--pos);color:var(--pos)}.chip.bad{border-color:var(--neg);color:var(--neg)}
.note{border-left:3px solid var(--warn);padding:6px 10px;background:var(--card);margin:8px 0;color:var(--fg)}
details{background:var(--card);border:1px solid var(--line);border-radius:6px;margin:8px 0;padding:10px 12px}
summary{cursor:pointer;font-weight:600}summary:focus-visible,a:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.kv{display:grid;grid-template-columns:200px 1fr;gap:4px 12px;margin-top:8px}.kv div:nth-child(odd){color:var(--muted)}
@media (max-width:600px){.kv{grid-template-columns:1fr}}
"""


def _pct(v, signed=True, digits=2):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "-"
    cls = "pos" if v > 0 else "neg" if v < 0 else ""
    return f'<span class="{cls}">{v * 100:{"+" if signed else ""}.{digits}f}%</span>'


def _e(x) -> str:
    return html.escape("" if x is None else str(x))


def _chip(state: str) -> str:
    cls = {"HEALTHY": "ok", "PROMOTE": "ok", "OK": "ok", "DISABLED": "bad", "HALT": "bad", "WORSE_THAN_CHAMPION": "bad"}.get(state, "warn")
    return f'<span class="chip {cls}">{_e(state)}</span>'


def market_panel(rcon) -> str:
    rows = rcon.execute("""
        WITH x AS (SELECT index_name, business_date, close_level,
                          ROW_NUMBER() OVER (PARTITION BY index_name ORDER BY business_date DESC) rk
                   FROM index_levels_canonical WHERE variant = 'PRICE' AND index_name IN (SELECT UNNEST(?)))
        SELECT index_name, MAX(CASE WHEN rk = 1 THEN close_level END), MAX(CASE WHEN rk = 2 THEN close_level END),
               MAX(CASE WHEN rk = 21 THEN close_level END), MAX(CASE WHEN rk = 1 THEN business_date END)
        FROM x WHERE rk <= 21 GROUP BY 1""", [list(INDICES)]).fetchall()
    tiles = []
    for name, last, prev, m20, d in sorted(rows, key=lambda r: INDICES.index(r[0])):
        if name == "India VIX":
            tiles.append(f'<div class="tile"><div class="k">{_e(name)} · {d}</div><div class="v">{last:,.2f}</div>'
                         f'<div class="sub">1d {last - prev:+.2f} pts</div></div>')
        else:
            tiles.append(f'<div class="tile"><div class="k">{_e(name)} · {d}</div><div class="v">{last:,.1f}</div>'
                         f'<div class="sub">1d {_pct(last / prev - 1)} · 20d {_pct(last / m20 - 1) if m20 else "-"}</div></div>')
    return '<div class="grid">' + "".join(tiles) + "</div>"


def build(rcon, ledger, *, day: date | None = None, capital: float = 1_000_000.0, analogue_pool=None) -> str:
    from ..decisions import forecast_ev as FE
    from ..decisions import plan as P
    from ..evaluation import champion as C
    from ..evaluation import setup_analogues as SA
    from ..forecasting.null import Climatology
    from ..opportunity import scanners as S
    from ..regime import breadth as BR
    from ..risk import killswitch as KS
    from . import board as BD
    from . import intraday_runner as IR

    asof = rcon.execute("SELECT MAX(business_date) FROM features").fetchone()[0]
    day = day or asof
    parts = [f"<title>Financial-Brain terminal</title><style>{CSS}</style><main>",
             f"<h1>Financial-Brain</h1><div class='sub'>Terminal for {day} · data through {asof} · written "
             f"{datetime.now().strftime('%Y-%m-%d %H:%M')} · paper only, no orders exist in this system</div>"]
    # ---------------------------------------------------------------- market
    b = BR.breadth(rcon, asof)
    parts += ["<h2>Market</h2>", market_panel(rcon),
              f"<div class='grid' style='margin-top:10px'><div class='tile'><div class='k'>Breadth</div><div class='v'>{_e(b['state']).upper()}</div>"
              f"<div class='sub'>{b['pct_above_ma200']:.0%} above 200d · {b['pct_up_20d']:.0%} up 20d · {b['pct_near_52w_high']:.0%} near high</div></div>"]
    ks = KS.load()
    parts.append(f"<div class='tile'><div class='k'>Kill switch</div><div class='v'>{_chip(ks['level'])}</div><div class='sub'>{_e('; '.join(ks.get('reasons', [])) or 'no trip')}</div></div></div>")
    # ---------------------------------------------------------------- opportunities
    cs = S.cross_section(rcon, asof)
    opps = S.scan(cs, asof, capital=capital)
    best = S.best_per_name(opps).sort_values("expected_net", ascending=False)
    clears = int((best["expected_net"] >= 0.005).sum())
    parts.append("<h2>Top opportunities</h2>")
    if clears == 0:
        parts.append("<div class='note'><b>Nothing clears costs today.</b> The best five by expected net alpha are shown as WATCH: "
                     "expected returns come from one model: what names in the same composite decile earned in the 2016-26 replay (in-sample), scaled to the horizon.</div>")
    rows = []
    for r in best.head(5).itertuples():
        rows.append(f"<tr><td><a href='#{_e(r.ticker)}'>{_e(r.ticker)}</a></td><td>{_e(r.type)} ({r.horizon}d)</td><td>{_e(r.evidence)}</td>"
                    f"<td class='r'>{_pct(r.expected_return)}</td><td class='r'>{r.cost * 100:.2f}%</td><td class='r'>{_pct(r.expected_net)}</td>"
                    f"<td class='r'>{r.price:,.1f}</td><td class='r'>{r.invalidation:,.1f}</td><td>{_e(r.flagged_by)}</td></tr>")
    parts.append("<div class='wrap'><table><tr><th>Stock</th><th>Opportunity</th><th>Evidence</th><th class='r'>Exp. alpha</th><th class='r'>Cost</th>"
                 "<th class='r'>Net</th><th class='r'>Price</th><th class='r'>Invalidation</th><th>Flagged by</th></tr>" + "".join(rows) + "</table></div>")
    # ---------------------------------------------------------------- active paper trades
    parts.append("<h2>Paper trades (latest session)</h2>")
    led_day = ledger.execute("SELECT MAX(day) FROM trades").fetchone()[0]
    if led_day:
        t = ledger.execute("""SELECT strategy, symbol, side, qty, entry_px, exit_px, exit_reason, net_pnl, mode FROM trades
                              WHERE day = ? ORDER BY net_pnl DESC NULLS LAST""", [led_day]).fetchall()
        tot = sum((x[7] or 0) for x in t)
        parts.append(f"<div class='sub'>{led_day} ({_e(t[0][8])}): {len(t)} trades, net Rs {tot:,.0f}</div><div class='wrap'><table>"
                     "<tr><th>Strategy</th><th>Stock</th><th>Side</th><th class='r'>Qty</th><th class='r'>Entry</th><th class='r'>Exit</th><th>Why</th><th class='r'>Net Rs</th></tr>"
                     + "".join(f"<tr><td>{_e(x[0])}</td><td>{_e(x[1])}</td><td>{_e(x[2])}</td><td class='r'>{x[3]}</td><td class='r'>{x[4]:,.2f}</td>"
                               f"<td class='r'>{(x[5] or 0):,.2f}</td><td>{_e(x[6] or 'open')}</td><td class='r {'pos' if (x[7] or 0) > 0 else 'neg'}'>{(x[7] or 0):,.0f}</td></tr>"
                               for x in t[:12]) + "</table></div>")
    else:
        parts.append("<div class='sub'>No paper trades yet.</div>")
    # ---------------------------------------------------------------- what changed
    ann = rcon.execute("""SELECT company, event_type, headline FROM announcements
                          WHERE business_date = ? AND materiality IN ('HIGH', 'CRITICAL') ORDER BY published_at DESC LIMIT 10""",
                       [asof]).fetchall()
    parts.append("<h2>What changed (material filings, data shown as filed)</h2><div class='wrap'><table><tr><th>Company</th><th>Event</th><th>Headline</th></tr>"
                 + "".join(f"<tr><td>{_e(a[0])}</td><td>{_e(a[1])}</td><td style='white-space:normal'>{_e((a[2] or '')[:140])}</td></tr>" for a in ann)
                 + "</table></div>")
    # ---------------------------------------------------------------- risk and evidence
    parts.append("<h2>Risk and evidence</h2><div class='wrap'><table><tr><th>Strategy</th><th>Mode</th><th class='r'>Trades</th><th class='r'>Net bp/trade</th><th>Health</th><th>Why</th></tr>")
    for e in IR.evidence(ledger):
        parts.append(f"<tr><td>{_e(e['strategy'])}</td><td>{_e(e['mode'])}</td><td class='r'>{e['trades']}</td><td class='r'>{e['net_bp_per_trade']:+.1f}</td>"
                     f"<td>{_chip(e.get('health', '-'))}</td><td style='white-space:normal'>{_e('; '.join(e.get('health_reasons', [])))}</td></tr>")
    for s in C.standings(ledger):
        parts.append(f"<tr><td>{_e(s['strategy'])} vs {C.CHAMPION}</td><td>locked OOS</td><td class='r'>{s.get('cohorts', 0)} cohorts</td><td class='r'>-</td>"
                     f"<td>{_chip(s['status'])}</td><td>promotion needs {C.MIN_COHORTS} cohorts at t >= {C.T_PROMOTE}</td></tr>")
    parts.append("</table></div>")
    # ---------------------------------------------------------------- drill-down
    parts.append("<h2>Why these names</h2>")
    for r in best.head(5).itertuples():
        row = cs[cs["ticker"] == r.ticker].iloc[0]
        px = [x[0] for x in rcon.execute("SELECT close_adj FROM adjusted_prices WHERE ticker = ? AND close_adj > 0 AND business_date <= ? ORDER BY business_date",
                                         [r.ticker, asof]).fetchall()]
        rng_txt = "-"
        if len(px) > 300:
            fc = Climatology().forecast(instrument=r.ticker, as_of=asof, prices=px[-750:], horizon=int(r.horizon), n_paths=1500)
            tr = np.sort(fc.terminal_returns())
            rng_txt = f"{tr[len(tr) // 10] * 100:+.1f}% / {tr[len(tr) // 2] * 100:+.1f}% / {tr[9 * len(tr) // 10] * 100:+.1f}% (10th / median / 90th, bootstrap of its own history)"
        pl = P.plan(ticker=r.ticker, price=float(r.price), vol_annual=float(row["vol_60"]), adv_inr=float(row["adv20"]),
                    capital_inr=capital, style="position" if r.horizon >= 20 else "swing", as_of=asof)
        ana = "-"
        if analogue_pool is not None:
            try:
                q = SA.query(analogue_pool, SA.target_vector(_with_surge(cs), r.ticker), asof)
                h5 = q["h5"]
                ana = (f"{q['k']} analogues: 5-day excess {h5['mean'] * 100:+.2f}% (pool {h5['pool_mean'] * 100:+.2f}%), "
                       f"win {h5['win_rate']:.0%} (pool {h5['pool_win_rate']:.0%}), edge t {h5['edge_t']:+.1f}; "
                       f"MFE/MAE 5d {q['mfe5_median'] * 100:+.1f}% / {q['mae5_median'] * 100:+.1f}%")
            except Exception as e:                    # noqa: BLE001
                ana = f"unavailable ({_e(str(e)[:60])})"
        spread = ledger.execute("SELECT AVG(spread_bp) FROM trades WHERE symbol = ? AND spread_bp IS NOT NULL", [r.ticker]).fetchone()[0]
        exe = (f"limit order at or inside the ask; measured spread {spread:.1f} bp" if spread else
               "limit order at or inside the ask; no live spread measured yet (bucket assumption applies)")
        exe += ("; enter in the morning rather than wait for a dip - the intraday half-life study found the "
                "day session moves the way the composite points (IC +0.046 09:30-15:15, t 6.9)")
        parts.append(
            f"<details id='{_e(r.ticker)}'><summary>{_e(r.ticker)} · {_e(r.type)} ({r.horizon}d) · net {r.expected_net * 100:+.2f}%</summary><div class='kv'>"
            f"<div>Why now</div><div>flagged by {_e(r.flagged_by)}; 12-1m {row['mom_12_1'] * 100:+.0f}%, vs 52w high {row['dist_52w_high'] * 100:+.0f}%, vol {row['vol_60'] * 100:.0f}%, 20d {row['ret_20d'] * 100:+.1f}%</div>"
            f"<div>Evidence</div><div>{_e(r.evidence)} (t {r.evidence_t if r.evidence_t is not None else '-'}); {_e(r.source)}</div>"
            f"<div>Historical analogues</div><div>{ana}</div>"
            f"<div>Range over {r.horizon} sessions</div><div>{rng_txt}</div>"
            f"<div>Expected net alpha</div><div>{r.expected_return * 100:+.2f}% gross - {r.cost * 100:.2f}% cost = {r.expected_net * 100:+.2f}%</div>"
            f"<div>Position size</div><div>{pl['shares']} shares = Rs {pl['notional']:,.0f} ({pl['weight']:.1%} of Rs {capital / 1e5:,.0f} lakh), sized by the {_e(pl['sized_by'])}</div>"
            f"<div>Entry / stop</div><div>reference {r.price:,.2f}; stop {pl['stop_price']:,.2f} ({pl['stop_distance']:.0%} below)</div>"
            f"<div>Target</div><div>none: the exit study found targets add no return; time exit by {pl['time_exit_on']}</div>"
            f"<div>Invalidation</div><div>the stop, or the composite falling into the bottom 20% at a monthly review</div>"
            f"<div>Execution</div><div>{exe}</div>"
            f"<div>Status</div><div>{_e(pl['status'])} - information, not advice</div></div></details>")
    parts.append("<p class='sub'>Information computed from public statistics. It does not know your finances, needs or tax, and nothing here is sent to a broker.</p></main>")
    return "\n".join(parts)


def _with_surge(cs: pd.DataFrame) -> pd.DataFrame:
    """The analogue target needs the analogue features; the scanner cross-section carries most of them."""
    c = cs.copy()
    c["volume_surge"] = c["turnover"] / c["adv20"]
    return c


def write(html_text: str, path: Path = OUT) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_text, encoding="utf-8")
    return path
