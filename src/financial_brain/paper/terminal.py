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


def _alpha(a: dict) -> str:
    """The itemised expected net alpha, as the review asked: every term visible, the net last."""
    if not a:
        return "-"
    items = [("expected return", a["expected_return"]), ("benchmark", -a["benchmark"]), ("fees and taxes", -a["fees"]),
             ("spread", -a["spread"]), ("impact", -a["impact"]), ("risk penalty", -a["risk_penalty"])]
    return (" ".join(f"{k} {v * 100:+.2f}% ·" for k, v in items)
            + f" <b>net {a['net'] * 100:+.2f}%</b> over {a['horizon']} sessions")


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


def _spark(df: pd.DataFrame) -> str:
    """Cumulative net rupees of the sold trades by exit day, against the universe on the same money."""
    d = df.sort_values("exit_day")
    if len(d) < 2:
        return ""
    a, b = d["net_rs"].cumsum().to_numpy(), (d["notional"] * d["bench"]).cumsum().to_numpy()
    lo, hi = float(min(a.min(), b.min(), 0)), float(max(a.max(), b.max(), 0))
    W, H, P = 640, 150, 34
    x = lambda i: P + (W - 2 * P) * i / (len(d) - 1)                       # noqa: E731
    y = lambda v: H - 18 - (H - 36) * (v - lo) / ((hi - lo) or 1)          # noqa: E731
    line = lambda s: " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(s))   # noqa: E731
    return (f"<div class='wrap' style='padding:8px;margin-top:10px'><svg viewBox='0 0 {W} {H}' width='100%' role='img' "
            f"aria-label='cumulative net P&amp;L of the book against the universe'>"
            f"<line x1='{P}' x2='{W - P}' y1='{y(0):.1f}' y2='{y(0):.1f}' stroke='var(--line)'/>"
            f"<polyline fill='none' stroke='var(--muted)' stroke-dasharray='4 3' stroke-width='1.5' points='{line(b)}'/>"
            f"<polyline fill='none' stroke='var(--accent)' stroke-width='2' points='{line(a)}'/>"
            f"<text x='{P}' y='12' font-size='11' fill='var(--muted)'>Rs {hi:,.0f}</text>"
            f"<text x='{P}' y='{H - 4}' font-size='11' fill='var(--muted)'>Rs {lo:,.0f} · sold {d['exit_day'].min()} to {d['exit_day'].max()}</text>"
            f"<text x='{W - P}' y='12' font-size='11' text-anchor='end' fill='var(--accent)'>book Rs {a[-1]:,.0f}</text>"
            f"<text x='{W - P}' y='26' font-size='11' text-anchor='end' fill='var(--muted)'>universe Rs {b[-1]:,.0f} (dashed)</text>"
            "</svg></div>")


def _d(x) -> str:
    return "-" if x is None or pd.isna(x) else str(pd.Timestamp(x).date())


def _sold_table(t: pd.DataFrame, title: str) -> str:
    return (f"<div><div class='sub' style='margin:10px 0 4px'>{title}</div><div class='wrap'><table><tr><th>Stock</th><th>Bought</th><th>Sold</th>"
            "<th class='r'>Net</th><th class='r'>vs universe</th><th>Why sold</th><th class='r'>Rs</th></tr>"
            + "".join(f"<tr><td>{_e(r.ticker)}</td><td>{_d(r.entry_day)} @ {r.entry_px:,.2f}</td><td>{_d(r.exit_day)} @ {r.exit_px:,.2f}</td>"
                      f"<td class='r'>{_pct(r.net)}</td><td class='r'>{_pct(r.excess)}</td><td>{_e(r.exit_reason)}</td>"
                      f"<td class='r {'pos' if r.net_rs > 0 else 'neg'}'>{r.net_rs:,.0f}</td></tr>" for r in t.itertuples())
            + "</table></div></div>")


MODE_LABEL = {"live": "Live (the evidence)",
              "replay": "Replay since 2023 (in-sample model: a demonstration)",
              "replay-week": "Last week, replayed (one week decides nothing: a demonstration of the mechanics)"}


def book_panel(book, spec=None, rcon=None) -> str:
    """Every suggestion the book bought, and how it was sold - losers included, best next to worst."""
    from ..decisions import plan as P
    from . import book as BK
    spec = spec or BK.POSITION
    out = [f"<h2>Paper book: {_e(spec.label)} ({spec.version})</h2>",
           f"<div class='sub'>The top {spec.top_n} each evening are bought on paper at the next close (at most "
           f"{spec.max_open} open, Rs {BK.CAPITAL / 1e5:.0f} lakh), sold at the stop or after {spec.horizon or 'their'} sessions, and charged the sized "
           "round trip. Every trade is kept: a record of only the winners would be a sales pitch, not evidence.</div>"]
    for mode in BK.modes(book, spec):
        label = MODE_LABEL.get(mode, mode)
        df = BK.frame(book, mode, spec)
        s = BK.summary(book, mode, spec)
        out.append(f"<h3 style='margin-top:14px'>{label}</h3>")
        tiles = [("Verdict", _chip(s["verdict"].split(" (")[0]), _e(s["verdict"][len(s["verdict"].split(" (")[0]):].strip(" ()")) or "&nbsp;")]
        if s["closed"]:
            tiles += [("Sold / open", f"{s['closed']} / {s['open']}", f"held {s['mean_held']:.0f} sessions on average, {s['stopped']:.0%} stopped"),
                      ("Made money", f"{s['win_rate']:.0%}", f"beat the universe {s['beat_universe']:.0%} of the time"),
                      ("Per trade, net", _pct(s["mean_net"]), f"vs universe {_pct(s['mean_excess'])} (t {s['t_excess']:+.1f}, {s['entry_days']} entry days)"),
                      ("Net P&L", f"Rs {s['net_rs']:,.0f}", f"universe on the same money Rs {s['bench_rs']:,.0f}")]
        else:
            tiles.append(("Sold / open", f"0 / {s['open']}", "nothing sold yet"))
        if "open_net_rs" in s:
            tiles.append(("Open P&L, net", f"Rs {s['open_net_rs']:,.0f}", f"universe on the same money Rs {s['open_bench_rs']:,.0f}"))
        out.append("<div class='grid'>" + "".join(f"<div class='tile'><div class='k'>{k}</div><div class='v'>{v}</div><div class='sub'>{n}</div></div>"
                                                   for k, v, n in tiles) + "</div>")
        if df.empty:
            continue
        held = df[df["status"] != "SOLD"].sort_values("net", ascending=False)
        if len(held):
            rows = []
            for r in held.itertuples():
                sell_by = P._add_sessions(pd.Timestamp(r.entry_day).date(), int(r.horizon)) if pd.notna(r.entry_day) else f"{r.horizon} sessions after entry"
                rows.append(f"<tr><td>{_e(r.ticker)}</td><td>{_e(r.status)}</td><td>{_d(r.signal_day)}</td>"
                            f"<td class='r'>{'-' if pd.isna(r.entry_px) else f'{r.entry_px:,.2f}'}</td>"
                            f"<td class='r'>{'-' if pd.isna(r.last_px) else f'{r.last_px:,.2f}'}</td>"
                            f"<td class='r'>{'-' if pd.isna(r.net) else _pct(r.net)}</td><td class='r'>{r.stop:,.2f}</td>"
                            f"<td>{sell_by}</td><td class='r'>{r.shares}</td>"
                            f"<td style='white-space:normal;min-width:260px'>{_e(getattr(r, 'why', None) or '')}</td></tr>")
            out.append("<div class='sub' style='margin-top:10px'>Holding now (net of the round trip; each sells at its stop or on the date shown)</div>"
                       "<div class='wrap'><table><tr><th>Stock</th><th>Status</th><th>Signal</th><th class='r'>Bought</th><th class='r'>Last</th>"
                       "<th class='r'>Net so far</th><th class='r'>Stop</th><th>Sell by</th><th class='r'>Shares</th><th>Why it was bought</th></tr>" + "".join(rows) + "</table></div>")
        sold = df[df["status"] == "SOLD"]
        if len(sold) and rcon is not None:
            try:
                at = BK.attribution(rcon, book, mode, spec, limit=150)
            except Exception:                           # noqa: BLE001 - the panel renders without it
                at = {}
            if at:
                out.append(f"<div class='sub' style='margin-top:8px'>Where the return came from, per trade (last {at['trades']} sold): "
                           f"market {at['market'] * 100:+.2f}% · sector {at['sector'] * 100:+.2f}% · <b>stock selection {at['selection'] * 100:+.2f}%</b> · "
                           f"costs {at['costs'] * 100:+.2f}% = net {at['net'] * 100:+.2f}%</div>")
        if len(sold) and rcon is not None and mode != "live":
            try:
                from ..evaluation import regret as RG
                cands = RG.book_candidates(book, rcon, spec.version, mode)
                recent = sorted(cands["day"].unique())[-60:]
                rs = RG.summary(RG.by_day(cands[cands["day"].isin(recent)]))
                if rs.get("days"):
                    out.append(f"<div class='sub'>Selection, last {rs['days']} signal days with alternatives: the names taken beat the names "
                               f"passed over by {rs['skill'] * 100:+.2f}% (t {rs['skill_t']:+.1f}) on {rs['hit']:.0%} of days; perfect hindsight would "
                               f"have added {rs['regret'] * 100:.2f}% a day's pick.</div>")
            except Exception:                           # noqa: BLE001
                pass
        if len(sold):
            out.append(_spark(sold))
            out.append("<div class='grid' style='grid-template-columns:repeat(auto-fit,minmax(min(420px,100%),1fr))'>"
                       + _sold_table(sold.nlargest(5, "net"), "Best five sold") + _sold_table(sold.nsmallest(5, "net"), "Worst five sold") + "</div>")
            out.append(_sold_table(sold.sort_values("exit_day", ascending=False).head(10), "Most recently sold (all outcomes)"))
    return "\n".join(out)


def my_portfolio_panel(rcon, asof, candidates) -> str:
    """The owner's Kite holdings (read only, stored locally): since cost, thesis health, book risk, alternatives."""
    from ..portfolio import my_book as MB
    if not MB.PATH.exists():
        return "<h2>My portfolio</h2><div class='sub'>Not synced yet: <code>fb my-portfolio --sync</code> after the Kite login.</div>"
    try:
        con = MB.connect(read_only=True)
        try:
            bk = MB.latest(con)
            day = con.execute("SELECT MAX(day) FROM holdings_snap").fetchone()[0]
        finally:
            con.close()
        if bk.empty:
            return "<h2>My portfolio</h2><div class='sub'>No holdings in the last sync.</div>"
        a = MB.analyse(rcon, bk, asof, candidates=candidates)
    except Exception as e:                              # noqa: BLE001 - the page still renders without it
        return f"<h2>My portfolio</h2><div class='sub'>unavailable: {_e(str(e)[:120])}</div>"
    rk = a["risk"]
    out = [f"<h2>My portfolio (Kite, read only, synced {day})</h2><div class='grid'>"
           f"<div class='tile'><div class='k'>Value</div><div class='v'>Rs {rk['value']:,.0f}</div><div class='sub'>P&L Rs {rk['pnl']:,.0f} since cost</div></div>"
           f"<div class='tile'><div class='k'>Risk</div><div class='v'>{rk['vol']:.0%} a year</div><div class='sub'>beta {rk['beta']:.2f} to the Nifty 500</div></div>"
           f"<div class='tile'><div class='k'>At risk to the stops</div><div class='v'>{rk['at_stops']:.1%}</div><div class='sub'>of the book, if every plan stop is hit</div></div>"
           f"<div class='tile'><div class='k'>Largest sector</div><div class='v'>{rk['top_sector'][1]:.0%}</div><div class='sub'>{_e(rk['top_sector'][0])}</div></div></div>"]
    rows = "".join(
        f"<tr><td>{_e(h['symbol'])}</td><td class='r'>{h['qty']:,.0f}</td><td class='r'>{h['avg']:,.2f}</td><td class='r'>{h['last']:,.2f}</td>"
        f"<td class='r'>{_pct(h['since_cost'])}</td><td class='r'>{h['stop']:,.2f}</td>"
        f"<td><div style='background:var(--line);width:90px;height:8px;border-radius:4px;display:inline-block;vertical-align:middle'>"
        f"<div style='background:{'var(--pos)' if h['score'] >= 60 else 'var(--warn)' if h['recommendation'] != 'EXIT BY RULE' else 'var(--neg)'};width:{h['score'] * 0.9:.0f}px;height:8px;border-radius:4px'></div></div> {h['score']:.0f}</td>"
        f"<td>{_chip(h['recommendation'])}</td><td style='white-space:normal'>{_e(h['reason'])}</td></tr>" for h in a["holdings"])
    out.append("<div class='wrap' style='margin-top:10px'><table><tr><th>Holding</th><th class='r'>Qty</th><th class='r'>Avg cost</th><th class='r'>Last</th>"
               "<th class='r'>Since cost</th><th class='r'>Plan stop</th><th>Thesis health</th><th>Rule state</th><th>What is failing</th></tr>" + rows + "</table></div>")
    if a["alternatives"] is not None and len(a["alternatives"]):
        out.append("<div class='sub' style='margin-top:8px'>Where the next unit of risk goes furthest, given what you hold (today's top names, by alpha per unit of risk added):</div>"
                   "<div class='wrap'><table><tr><th>Stock</th><th class='r'>Net alpha</th><th class='r'>Correlation with your book</th><th class='r'>Risk added</th></tr>"
                   + "".join(f"<tr><td><a href='#{_e(r.ticker)}'>{_e(r.ticker)}</a></td><td class='r'>{_pct(r.alpha)}</td><td class='r'>{r.corr_to_book:+.2f}</td>"
                             f"<td class='r'>{r.added_risk * 100:.2f}%</td></tr>" for r in a["alternatives"].head(5).itertuples()) + "</table></div>")
    out.append("<div class='sub'>EXIT BY RULE appears only for the plan's tested exits (below the stop from your cost, or the composite in "
               "the bottom 20%); the health score's bands are descriptive. About your own positions, not advice.</div>")
    return "\n".join(out)


def _my_book_analysis(rcon, asof, candidates):
    from ..portfolio import my_book as MB
    if not MB.PATH.exists():
        return None
    con = MB.connect(read_only=True)
    try:
        bk = MB.latest(con)
        entries = {s_: {"first_day": d, "composite_pct": c, "price": p_}
                   for s_, d, c, p_ in con.execute("SELECT * FROM holding_entry").fetchall()} \
            if con.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'holding_entry'").fetchone()[0] else {}
    finally:
        con.close()
    return None if bk.empty else MB.analyse(rcon, bk, asof, candidates=candidates, entries=entries)


def decision_summary(rcon, asof, cs, best, breadth_state, ks_level, my) -> str:
    """The front door: the funnel from every tradable name to the few that improve this book, the open positions
    with an action each, and today's risks."""
    from ..portfolio import next_capital as NC
    tradable = rcon.execute("SELECT COUNT(*) FROM features WHERE business_date = ?", [asof]).fetchone()[0]
    flagged = best
    survive = flagged[flagged["expected_net"] > 0]
    gate = survive[survive["evidence"] == "REPLICATED_HELD_OUT"]
    regime_ok = ks_level == "OK" and (breadth_state or "").lower() != "weak"
    fits = gate if regime_ok else gate.iloc[0:0]
    improve = fits.iloc[0:0]
    if my is not None and len(fits):
        try:
            cand = fits.head(50)[["ticker", "expected_net"]].rename(columns={"expected_net": "net"})
            names = list(set(cand["ticker"]) | set(my["book_inr"]))
            px = rcon.execute("""SELECT ticker, business_date, close_adj FROM adjusted_prices WHERE business_date <= ?
                                 AND business_date > ?::DATE - INTERVAL 400 DAY AND ticker IN (SELECT UNNEST(?))""", [asof, asof, names]).df()
            cov = px.pivot_table(index="business_date", columns="ticker", values="close_adj").pct_change(fill_method=None).tail(250).cov() * 252
            t = NC.compare(cand, cov, my["book_inr"])
            improve = t[(t["alpha_per_risk"] > 0) & (t["corr_to_book"] < 0.5)]
        except Exception:                                # noqa: BLE001
            pass
    steps = [(f"{tradable:,}", "names traded today"), (f"{len(flagged):,}", "flagged by a scanner"),
             (f"{len(survive):,}", "expected net alpha above zero after costs"),
             (f"{len(gate):,}", "rest on signals whose IC replicated and held out (but the ranking that orders them, v1, "
                                "failed its 2022-26 walk-forward - see Evidence, by kind)"),
             (f"{len(fits):,}", "pass the regime gate (kill switch OK, breadth not weak - a stated rule)"),
             (f"{len(improve):,}" if my is not None else "-", "improve your book (alpha per unit of added risk > 0, correlation < 0.5)")]
    out = ["<h2>Today, in one line each</h2><div class='wrap' style='padding:10px 14px'>"
           + " &rarr; ".join(f"<b>{n}</b> {_e(w)}" for n, w in steps) + "</div>"]
    if my is not None:
        rows = "".join(
            f"<tr><td>{_e(h['symbol'])}</td><td class='r'>{_pct(h['since_cost'])}</td><td class='r'>{h['score']:.0f}%</td>"
            f"<td>{_chip(h['exit']['action'])}" + (f" {h['exit']['reduce_fraction']:.0%}" if h['exit']['action'] == 'REDUCE' else '') + "</td>"
            f"<td>{'tested rule' if h['exit']['tested_basis'] else 'rule of thumb'}</td>"
            f"<td style='white-space:normal'>{_e('; '.join(v['view'] + ' ' + v['action'] + ': ' + v['why'] for v in h['exit']['views'] if v['action'] != 'HOLD') or 'all six views HOLD')}</td></tr>"
            for h in my["holdings"])
        out.append("<div class='sub' style='margin-top:8px'>Open positions - the exit brain's six views (thesis, technical, time, risk, event, portfolio):</div>"
                   "<div class='wrap'><table><tr><th>Position</th><th class='r'>Since cost</th><th class='r'>Thesis health</th><th>Action</th>"
                   "<th>Basis</th><th>Views not at HOLD</th></tr>" + rows + "</table></div>")
    risks = []
    if (breadth_state or "").lower() in ("narrow", "weak"):
        risks.append(f"breadth is {breadth_state}: few stocks are carrying the index")
    if ks_level != "OK":
        risks.append(f"kill switch: {ks_level}")
    if my is not None and my["risk"].get("top_sector") and my["risk"]["top_sector"][1] > 0.5:
        risks.append(f"{my['risk']['top_sector'][1]:.0%} of your book is in {my['risk']['top_sector'][0]}")
    if my is not None and my["risk"].get("beta", 0) > 1.2:
        risks.append(f"your book's beta is {my['risk']['beta']:.2f}: a 1% market fall is about {my['risk']['beta']:.1f}% for you")
    vix = rcon.execute("""SELECT close_level FROM index_levels_canonical WHERE index_name = 'India VIX' AND variant = 'PRICE'
                          AND business_date <= ? ORDER BY business_date DESC LIMIT 6""", [asof]).fetchall()
    if len(vix) == 6 and vix[0][0] > 1.2 * vix[5][0]:
        risks.append(f"India VIX up {vix[0][0] / vix[5][0] - 1:.0%} in 5 sessions")
    out.append("<div class='sub' style='margin-top:8px'>Risks: " + (" · ".join(_e(r) for r in risks) if risks else "none flagged by the rules") + "</div>")
    return "\n".join(out)


def evidence_panel(ledger, book) -> str:
    from ..evaluation import evidence_tiers as ET
    rows = ET.ledger(intraday=ledger, book=book)
    mark = lambda t: "&#10003;" if t.get("passed") else ("&#10007;" if t.get("passed") is False else "&middot;")    # noqa: E731
    return ("<h2>Evidence, by kind (never mixed)</h2><div class='wrap'><table><tr><th>Strategy</th><th>Historical</th><th>Walk-forward</th>"
            "<th>Paper-forward</th><th>Live-forward</th><th>Confidence</th></tr>"
            + "".join(f"<tr><td>{_e(x['strategy'])}</td>"
                      + "".join(f"<td style='white-space:normal'>{mark(x['tiers'][k])} {_e(x['tiers'][k]['result'])}</td>"
                                for k in (ET.HISTORICAL, ET.WALK, ET.PAPER, ET.LIVE))
                      + f"<td>{_chip(x['confidence'])}</td></tr>" for x in rows) + "</table></div>"
            "<div class='sub'>Confidence comes from the forward tiers only: NONE without a passed walk-forward test, LIMITED until "
            "the paper record reaches 100 trades, and never above MODERATE while there is no live record.</div>")


def trader_section(rcon, ledger, cs, best, asof, *, capital, analogue_pool, breadth_state, clears, top: int = 10, my=None) -> str:
    """What should I trade today: the top names by expected net alpha, each answered the way a trader asks."""
    from ..decisions import plan as P
    from ..evaluation import setup_analogues as SA
    from ..forecasting.null import Climatology
    from ..ingest import kite_fno as KF
    from ..opportunity import scanners as S
    from . import trader_view as TV
    out = [f"<h2>What should I trade today? Top {top} by expected net alpha</h2>"]
    if clears == 0:
        out.append("<div class='note'><b>Nothing clears costs by a margin today.</b> These are the best available, shown so you can see "
                   "why - not because they are good trades. Expected returns come from one measured model (what names in the same "
                   "composite decile earned, 2016-26, in-sample).</div>")
    tied = best.head(top)["type"].value_counts()
    if len(tied) and tied.iloc[0] >= top * 0.7:
        out.append(f"<div class='note'><b>Read with care:</b> {tied.iloc[0]} of these {top} were flagged by the {_e(tied.index[0])} scanner. "
                   "Every name in a composite decile gets the same expected return and the ties are ordered by cost and risk, which "
                   "favour large, calm names; the half-life table found the low-volatility end of the ranking does not pay long-only "
                   "(top quintile -0.30% over 60 sessions, momentum +1.65%). The pre-registered fix, ranking v2, is shown below as a challenger: it beat this list "
                   "in every test split, but not significantly, so it has not replaced it.</div>")
    comp = TV.components(cs)
    dec = S.decile_expectation(cs)
    fno = {}
    try:
        day = ledger.execute("SELECT MAX(day) FROM fno_snapshots").fetchone()[0]
        if day:
            fno = {x["symbol"]: x for x in KF.positioning(ledger, day)}
    except Exception:                                   # noqa: BLE001 - no snapshots yet
        fno = {}
    shown = best.head(top)
    rows, cards, sizing, full = [], [], [], []
    from ..decisions import opportunity_card as OC
    for k, r in enumerate(shown.itertuples(), 1):
        i = cs.index[cs["ticker"] == r.ticker][0]
        row = cs.loc[i]
        plan = P.plan(ticker=r.ticker, price=float(r.price), vol_annual=float(row["vol_60"]), adv_inr=float(row["adv20"]),
                      capital_inr=capital, style="position" if r.horizon >= 20 else "swing", as_of=asof)
        plan["time_exit_on"] = P._add_sessions(asof, int(r.horizon))    # the opportunity's horizon, not the style default
        ana = None
        if analogue_pool is not None:
            try:
                ana = SA.query(analogue_pool, SA.target_vector(_with_surge(cs), r.ticker), asof)
            except Exception:                           # noqa: BLE001
                ana = None
        px = [x[0] for x in rcon.execute("SELECT close_adj FROM adjusted_prices WHERE ticker = ? AND close_adj > 0 AND business_date <= ? ORDER BY business_date",
                                         [r.ticker, asof]).fetchall()]
        rets = None
        if len(px) > 300:
            rets = Climatology().forecast(instrument=r.ticker, as_of=asof, prices=px[-750:], horizon=int(r.horizon), n_paths=1500).terminal_returns()
        o = TV.outcome(rets, plan["stop_distance"]) if rets is not None else {}
        try:
            from ..features import fundamentals_pit as FP
            snap = FP.snapshot(FP.as_known_on(rcon, r.ticker, asof))
            raw = rcon.execute("SELECT close_raw FROM adjusted_prices WHERE ticker = ? AND business_date <= ? ORDER BY business_date DESC LIMIT 1",
                               [r.ticker, asof]).fetchone()
            fund = {**FP.ratios(snap, float(raw[0])), "latest_period": snap.get("latest_period")} if snap and raw else None
        except Exception:                               # noqa: BLE001 - no observation table yet
            fund = None
        sid = f"r{k}"
        sizing.append({"id": sid, "price": float(r.price), "stop": float(plan["stop_price"]), "adv": float(row["adv20"])})
        rows.append(f"<tr><td class='r'>{k}</td><td><a href='#{_e(r.ticker)}'>{_e(r.ticker)}</a></td><td>LONG</td><td class='r'>{_pct(r.expected_net)}</td>"
                    f"<td>{r.horizon} sessions</td><td class='r'>{o.get('p_up', float('nan')):.0%}</td><td class='r'>{o.get('reward_risk', float('nan')):.2f}</td>"
                    f"<td class='r'>{plan['stop_distance']:.1%}</td><td>{_e(r.evidence)}</td></tr>")
        sigma_h = float(row["vol_60"]) * np.sqrt(int(r.horizon) / 252)
        full.append(OC.FullOpportunity(
            instrument=str(r.instrument), ticker=r.ticker, timestamp=str(asof), opportunity_type=r.type, direction="long",
            horizon=int(r.horizon), market_regime=breadth_state, sector_regime={},
            technical_state={k2: float(row[k2]) for k2 in ("mom_12_1", "dist_52w_high", "vol_60", "ret_20d", "rsi_14") if pd.notna(row.get(k2))},
            fundamental_state=fund or {}, derivative_state=fno.get(r.ticker) or {}, forecast_distribution=r.distribution,
            expected_return=float(r.expected_return), expected_downside=-o.get("loss_if_wrong", float("nan")) if o else None,
            expected_cost=float(r.cost), alpha=r.alpha, chain=OC.alpha_chain(float(r.expected_net), sigma_h, None),
            probability_of_profit=o.get("p_up") if o else None,
            mfe_expectation=ana["mfe5_median"] if ana else None, mae_expectation=ana["mae5_median"] if ana else None,
            invalidation={"stop": plan["stop_price"], "rules": ["close below the stop", "composite in the bottom 20%"]},
            liquidity={"adv20_inr": float(row["adv20"])}, capacity_inr=OC.capacity_inr(float(row["adv20"])),
            position_size={k2: plan[k2] for k2 in ("shares", "notional", "weight", "stop_distance")},
            evidence={"status": r.evidence, "ids": r.evidence_ids, "experiment": r.experiment_id}))
        cards.append(TV.card(r, row, comp.loc[i], rank=k, universe=len(cs), decile_mean=float(dec.loc[i]), analogue=ana,
                             breadth_state=breadth_state, fno=fno.get(r.ticker), returns=rets, plan=plan, sid=sid, fund=fund))
    out.append("<div class='wrap'><table><tr><th class='r'>#</th><th>Stock</th><th>Direction</th><th class='r'>Net alpha</th><th>Horizon</th>"
               "<th class='r'>P(up)</th><th class='r'>Reward/risk</th><th class='r'>Stop</th><th>Evidence</th></tr>" + "".join(rows) + "</table></div>")
    out.append(TV.calculator(sizing))
    try:                                                # the day's list, kept so your trades can be compared with it later
        ledger.execute("CREATE TABLE IF NOT EXISTS terminal_lists (day VARCHAR, rank INTEGER, ticker VARCHAR, lineage VARCHAR, net DOUBLE, PRIMARY KEY (day, rank))")
        ledger.executemany("INSERT OR REPLACE INTO terminal_lists VALUES (?,?,?,?,?)",
                           [(str(asof), k, r.ticker, str(r.instrument), float(r.expected_net)) for k, r in enumerate(shown.itertuples(), 1)])
    except Exception:                                   # noqa: BLE001 - a read-only ledger skips it
        pass
    try:
        import json as _json
        OUT.parent.mkdir(parents=True, exist_ok=True)
        (OUT.parent / "opportunities.json").write_text("[" + ",".join(x.to_json() for x in full) + "]", encoding="utf-8")
    except Exception:                                   # noqa: BLE001
        pass
    if my is not None and my.get("cov") is not None:
        try:
            from ..portfolio import next_capital as NC
            names = list(set(shown["ticker"]) | set(my["book_inr"]))
            px = rcon.execute("""SELECT ticker, business_date, close_adj FROM adjusted_prices WHERE business_date <= ?
                                 AND business_date > ?::DATE - INTERVAL 400 DAY AND ticker IN (SELECT UNNEST(?))""", [asof, asof, names]).df()
            cov = px.pivot_table(index="business_date", columns="ticker", values="close_adj").pct_change(fill_method=None).tail(250).cov() * 252
            cand = shown[["ticker", "expected_net", "cost"]].rename(columns={"expected_net": "net"})
            cmp_ = NC.compare(cand, cov, my["book_inr"])
            alloc = NC.allocate(cand, cov, my["book_inr"], budget=100_000)
            top3 = cmp_.head(3)
            out.append("<h3 style='margin-top:14px'>Before you buy: the alternatives, given what you hold</h3><div class='wrap'><table>"
                       "<tr><th></th>" + "".join(f"<th class='r'>{_e(t)}</th>" for t in top3["ticker"]) + "</tr>"
                       + "".join(f"<tr><td>{lab}</td>" + "".join(f"<td class='r'>{fn(v)}</td>" for v in top3[col]) + "</tr>"
                                 for lab, col, fn in (("Expected net alpha", "net", lambda v: f"{v * 100:+.2f}%"),
                                                      ("Volatility (a year)", "vol", lambda v: f"{v:.0%}"),
                                                      ("Cost", "cost", lambda v: f"{v * 100:.2f}%"),
                                                      ("Correlation with your book", "corr_to_book", lambda v: f"{v:+.2f}"),
                                                      ("Alpha per unit of added risk", "alpha_per_risk", lambda v: f"{v:.3f}")))
                       + f"</table></div><div class='sub'>{_e(NC.reason(cmp_))}.</div>")
            out.append("<div class='sub' style='margin-top:6px'><b>Where the next Rs 1 lakh goes</b> (alpha minus the variance it adds to your book, "
                       "Rs 10,000 at a time, at most 10% of the book in a name): "
                       + ", ".join(f"{_e(t)} Rs {v:,.0f}" for t, v in alloc["allocation"].items())
                       + f"{', ' if alloc['allocation'] else ''}cash Rs {alloc['cash']:,.0f}.</div>")
        except Exception as e:                          # noqa: BLE001
            out.append(f"<div class='sub'>comparison unavailable: {_e(str(e)[:80])}</div>")
    out += cards
    try:
        from ..opportunity import ranking_v2 as R2
        v2 = R2.rank(cs, horizon=60, capital=capital).head(top)
        out.append("<h3 style='margin-top:14px'>Challenger view: ranking v2 (momentum + 52-week high, no tie-breaking by cost)</h3>"
                   "<div class='sub'>Not qualified: in its 2022-26 test it beat this list by 0.35-0.40% a month and plain momentum by 0.3%, "
                   "but not significantly (t 0.5-0.8), and its own net was +0.01% / +0.19% a month. It competes live from 2026-10-07. "
                   "<b>Its expected returns are a straight-line slope frozen on 2016-21: in 2022-26 its top 20 earned about a third of what "
                   "that slope implies at these scores</b>, so read +5% as roughly +1.5%.</div>"
                   "<div class='wrap'><table><tr><th class='r'>#</th><th>Stock</th><th class='r'>Score</th><th class='r'>Expected 60d</th>"
                   "<th class='r'>Costs</th><th class='r'>Net</th><th class='r'>12-1 mom.</th><th class='r'>vs 52w high</th></tr>"
                   + "".join(f"<tr><td class='r'>{k}</td><td>{_e(r.ticker)}</td><td class='r'>{r.score:+.2f}</td><td class='r'>{_pct(r.expected_return)}</td>"
                             f"<td class='r'>{r.cost * 100:.2f}%</td><td class='r'>{_pct(r.expected_net)}</td><td class='r'>{r.mom_12_1 * 100:+.0f}%</td>"
                             f"<td class='r'>{r.dist_52w_high * 100:+.0f}%</td></tr>" for k, r in enumerate(v2.itertuples(), 1)) + "</table></div>")
    except Exception as e:                              # noqa: BLE001 - the page renders without it
        out.append(f"<div class='sub'>ranking v2 unavailable: {_e(str(e)[:80])}</div>")
    try:
        from ..evaluation import learned_model as LM
        lm = LM.score_day(rcon, asof).head(top)
        out.append("<h3 style='margin-top:14px'>Challenger view: the learned model (161 library features combined, walk-forward)</h3>"
                   "<div class='sub'>Qualified as a live challenger: in 2022-26 its top 20 earned +0.77% a month over the 500 most liquid names after "
                   "costs (t 2.3), ahead of plain momentum by 0.29% a month - not significantly (t 0.5) - and it lost to momentum in 2019-21. "
                   "It ranks; it does not give a return figure. Competes live from 2026-10-07.</div><div class='wrap'><table>"
                   "<tr><th class='r'>#</th><th>Stock</th><th class='r'>Model rank score</th></tr>"
                   + "".join(f"<tr><td class='r'>{k}</td><td>{_e(r.ticker)}</td><td class='r'>{r.pred:.3f}</td></tr>"
                             for k, r in enumerate(lm.itertuples(), 1)) + "</table></div>")
    except Exception as e:                              # noqa: BLE001
        out.append(f"<div class='sub'>learned model unavailable: {_e(str(e)[:80])}</div>")
    wn = TV.watched_not_traded(best, set(shown["ticker"]))
    if wn:
        out.append("<h3 style='margin-top:14px'>Watched, not on the list</h3><div class='wrap'><table><tr><th>Stock</th><th>Type</th>"
                   "<th class='r'>Expected before costs</th><th class='r'>Net</th><th>Why not</th></tr>"
                   + "".join(f"<tr><td>{_e(w['ticker'])}</td><td>{_e(w['type'])}</td><td class='r'>{_pct(w['expected_return'])}</td>"
                             f"<td class='r'>{_pct(w['net'])}</td><td>{_e(w['why'])}</td></tr>" for w in wn) + "</table></div>")
    out.append("<div class='sub'>Long only: short positions are intraday-only in the cash market. P(up), reward/risk and the range come from "
               "each name's own price history, not conditioned on the signal. Information, not advice.</div>")
    return "\n".join(out)


def build(rcon, ledger, *, day: date | None = None, capital: float = 1_000_000.0, analogue_pool=None, book=None) -> str:
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
    try:
        my = _my_book_analysis(rcon, asof, best.head(10))
    except Exception:                                   # noqa: BLE001
        my = None
    parts.append(decision_summary(rcon, asof, cs, best, b.get("state"), ks.get("level", "OK"), my))
    parts.append(trader_section(rcon, ledger, cs, best, asof, capital=capital, analogue_pool=analogue_pool,
                                breadth_state=b.get("state"), clears=clears, my=my))
    parts.append(my_portfolio_panel(rcon, asof, best.head(10)))
    parts.append(evidence_panel(ledger, book))
    # ---------------------------------------------------------------- swing candidates (top 50)
    from ..decisions import plan as _P
    from . import book as BK
    sw = BK.swing_ranking(rcon, asof, BK.SWING, capital).head(BK.SWING.top_n)
    parts.append(f"<h2>Swing candidates: top {BK.SWING.top_n} for the next {BK.SWING.horizon} sessions</h2>")
    if len(sw):
        n_clear = int((sw["expected_net"] > 0).sum())
        parts.append("<div class='note'><b>Evidence for swing trading: NONE_DEMONSTRATED.</b> Every 5-10 session setup tested lost "
                     f"0.2-0.6% a trade after costs. {n_clear} of these {len(sw)} clear their own round trip on the model's expectation "
                     f"(mean {sw['expected_net'].mean() * 100:+.2f}% net). They are paper-traded below so the record, not this table, says whether that is too pessimistic.</div>")
        sell_by = _P._add_sessions(_P._add_sessions(asof, 1), BK.SWING.horizon)
        rows = "".join(
            f"<tr><td class='r'>{i}</td><td>{_e(r.ticker)}</td><td class='r'>{r.price:,.2f}</td><td class='r'>{r.invalidation:,.2f}</td>"
            f"<td>{sell_by}</td><td class='r'>{r.shares}</td><td class='r'>{r.shares * r.price:,.0f}</td><td class='r'>{_pct(r.expected_return)}</td>"
            f"<td class='r'>{r.cost * 100:.2f}%</td><td class='r'>{_pct(r.expected_net)}</td><td class='r'>{r.vol_60 * 100:.0f}%</td></tr>"
            for i, r in enumerate(sw.itertuples(), 1))
        parts.append("<details open><summary>All " + str(len(sw)) + " names: buy at the next close, sell at the stop or by the date shown</summary><div class='wrap' style='margin-top:8px'><table>"
                     "<tr><th class='r'>#</th><th>Stock</th><th class='r'>Price</th><th class='r'>Stop</th><th>Sell by</th><th class='r'>Shares</th>"
                     "<th class='r'>Rs</th><th class='r'>Exp. 7d</th><th class='r'>Cost</th><th class='r'>Net</th><th class='r'>Vol</th></tr>" + rows + "</table></div></details>")
    if book is not None:
        parts.append(book_panel(book, BK.SWING, rcon))
    if book is not None:
        parts.append(book_panel(book, BK.POSITION, rcon))
        parts.append(book_panel(book, BK.LEARNED, rcon))
        parts.append(book_panel(book, BK.MOMENTUM, rcon))
    # ---------------------------------------------------------------- intraday paper trades
    parts.append("<h2>Intraday paper trades (live-v1: ORB and GAP, flat by 15:15)</h2>")
    week = ledger.execute("""SELECT day, mode, strategy, COUNT(*), SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END), SUM(gross_pnl), SUM(costs), SUM(net_pnl)
                             FROM trades WHERE exit_ts IS NOT NULL GROUP BY 1, 2, 3 ORDER BY 1 DESC, 3 LIMIT 20""").fetchall()
    if week:
        tot = sum(w[7] or 0 for w in week)
        parts.append(f"<div class='sub'>By day: {sum(w[3] for w in week)} trades, gross Rs {sum(w[5] or 0 for w in week):,.0f}, "
                     f"costs Rs {sum(w[6] or 0 for w in week):,.0f}, net <span class='{'pos' if tot > 0 else 'neg'}'>Rs {tot:,.0f}</span>. "
                     "Replayed days use Kite minute bars and the same rules; live days are the evidence.</div><div class='wrap'><table>"
                     "<tr><th>Day</th><th>Mode</th><th>Strategy</th><th class='r'>Trades</th><th class='r'>Won</th><th class='r'>Gross Rs</th><th class='r'>Costs Rs</th><th class='r'>Net Rs</th></tr>"
                     + "".join(f"<tr><td>{w[0]}</td><td>{_e(w[1])}</td><td>{_e(w[2])}</td><td class='r'>{w[3]}</td><td class='r'>{w[4]}</td>"
                               f"<td class='r'>{(w[5] or 0):,.0f}</td><td class='r'>{(w[6] or 0):,.0f}</td><td class='r {'pos' if (w[7] or 0) > 0 else 'neg'}'>{(w[7] or 0):,.0f}</td></tr>"
                               for w in week) + "</table></div>")
    try:
        from . import execution as XA
        xa = XA.attribution(ledger)
    except Exception:                                   # noqa: BLE001 - older ledgers lack the decision prices
        xa = []
    if xa:
        parts.append("<div class='sub' style='margin-top:10px'>Where the money went: what the signal earned at its trigger prices, what execution "
                     "cost (spread and fills), and the charges - the three add up to net.</div><div class='wrap'><table>"
                     "<tr><th>Strategy</th><th>Mode</th><th class='r'>Trades</th><th class='r'>Signal Rs</th><th class='r'>Execution Rs</th>"
                     "<th class='r'>Charges Rs</th><th class='r'>Net Rs</th><th class='r'>Net bp/trade notional</th></tr>"
                     + "".join(f"<tr><td>{_e(x['strategy'])}</td><td>{_e(x['mode'])}</td><td class='r'>{x['trades']}</td>"
                               f"<td class='r {'pos' if x['signal_inr'] > 0 else 'neg'}'>{x['signal_inr']:,.0f}</td><td class='r neg'>{x['execution_inr']:,.0f}</td>"
                               f"<td class='r neg'>{x['charges_inr']:,.0f}</td><td class='r {'pos' if x['net_inr'] > 0 else 'neg'}'>{x['net_inr']:,.0f}</td>"
                               f"<td class='r'>{x['net_bp']:+.1f}</td></tr>" for x in xa) + "</table></div>")
    try:
        from . import loss_review as LR
        wm = LR.with_market_view(ledger)
    except Exception:                                   # noqa: BLE001 - older ledgers have no context columns
        wm = None
    if wm:
        w, a = wm["with"], wm["against"]
        parts.append(f"<div class='note'><b>Your rule, tracked on the real trades: only with the market.</b> Over {wm['days']} day(s) of trades with context: "
                     f"with the market {w['trades']} trades, Rs {w['net_inr']:,.0f} ({w['bp']:+.1f} bp a trade); against it {a['trades']} trades, "
                     f"Rs {a['net_inr']:,.0f} ({a['bp']:+.1f} bp). {_e(wm['status'])}: on 2024-26 minute data the same filter doubled gross in the "
                     "first year and added nothing in the held-out year. This line keeps checking it live.</div>")
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
