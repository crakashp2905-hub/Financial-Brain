"""Best-effort, atomic paper viewer snapshots published by the owning runner."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))


def view_path(ledger: Path) -> Path:
    return ledger.parent / "paper_live_view" / ledger.stem / "index.html"


def render(engine=None, *, now=None, status="waiting for next engine start", polls=0, prices=None,
           quote_at=None) -> str:
    now = now or datetime.now(IST)
    prices = prices or {}
    trades = engine.trades if engine is not None else []
    closed = [t for t in trades if not t.is_open]
    opened = [t for t in trades if t.is_open]

    def money(value):
        return "—" if value is None else f"₹{value:,.2f}"

    def cell(value):
        return f"<td>{escape(str(value))}</td>"

    def table(rows, is_open):
        heads = ["Strategy", "Symbol", "Side", "Qty", "Entry time (IST)", "Entry", "Stop", "Target"]
        heads += ["Last quote", "Unrealised gross"] if is_open else ["Exit time (IST)", "Exit", "Reason", "Costs", "Net P&L"]
        body = []
        for t in reversed(rows):
            values = [t.strategy, t.symbol, t.side, t.qty, str(t.entry_ts)[11:19], money(t.entry_px), money(t.stop), money(t.target)]
            if is_open:
                mark = prices.get(t.symbol)
                gross = None if mark is None else (mark - t.entry_px) * t.qty * (1 if t.side == "long" else -1)
                values += [money(mark), money(gross)]
            else:
                values += [str(t.exit_ts)[11:19], money(t.exit_px), t.exit_reason, money(t.costs), money(t.net_pnl)]
            body.append("<tr>" + "".join(cell(v) for v in values) + "</tr>")
        if not body:
            body.append(f'<tr><td colspan="{len(heads)}">No trades recorded in this feed.</td></tr>')
        return '<div class="scroll"><table><thead><tr>' + ''.join(f'<th>{h}</th>' for h in heads) + '</tr></thead><tbody>' + ''.join(body) + '</tbody></table></div>'

    marks = [(prices[t.symbol] - t.entry_px) * t.qty * (1 if t.side == "long" else -1)
             for t in opened if t.symbol in prices]
    unrealised = sum(marks) if len(marks) == len(opened) else None
    ready = engine is not None
    net = sum(t.net_pnl for t in closed) if ready else None
    metric = lambda label, value: f'<div class="card"><span>{label}</span><strong>{escape(str(value))}</strong></div>'
    cards = metric("Realised net · after modelled costs", money(net))
    cards += metric("Open P&L · gross, before exit costs", money(unrealised) if ready else "—")
    cards += metric("Open / closed trades", f"{len(opened)} / {len(closed)}" if ready else "—")
    cards += metric("Winning closed trades", f"{sum(t.net_pnl > 0 for t in closed)} / {len(closed)}" if ready else "—")
    stamp = now.isoformat() if ready else ""
    quote = quote_at.isoformat() if quote_at else ""
    day = engine.day if ready else now.date().isoformat()
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="20"><title>Finance-Brain · Live paper trades</title>
<style>body{{margin:0;background:#0c1421;color:#e4edf8;font:15px system-ui}}main{{max-width:1500px;margin:auto;padding:32px}}h1{{font-size:32px;margin:8px 0}}h2{{margin-top:32px}}.muted,span{{color:#a8b8cc}}.tag{{color:#63d9c4;letter-spacing:2px;font-size:12px}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin:24px 0}}.card,.notice{{background:#152238;border:1px solid #2b405d;padding:20px;border-radius:12px}}strong{{display:block;font-size:26px;margin-top:12px}}.notice{{border-left:4px solid #e3b562}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;white-space:nowrap}}th,td{{padding:13px;text-align:right;border-bottom:1px solid #293a51}}th{{color:#a8b8cc;font-size:12px}}th:nth-child(-n+3),td:nth-child(-n+3){{text-align:left}}button{{background:#183e50;color:#fff;border:1px solid #417486;padding:10px 18px;border-radius:8px;cursor:pointer}}footer{{margin-top:28px;color:#a8b8cc;line-height:1.7}}</style>
<main><div class="tag">FINANCE-BRAIN / PAPER ONLY</div><h1>Live paper trades</h1><p class="muted">{escape(day)} · IST · refreshes every 20 seconds</p>
<div class="notice"><b>{escape(status)}</b><div id="freshness" data-stamp="{stamp}" data-quote="{quote}">{'Snapshot published ' + escape(now.strftime('%H:%M:%S IST')) if ready else 'No live feed yet. Today’s existing engine continues unchanged. Updates begin at the next engine start.'}</div></div>
<div class="cards">{cards}</div><button onclick="location.reload()">Refresh now</button>
<h2>Open positions</h2>{table(opened, True)}<h2>Closed trades</h2>{table(closed, False)}
<footer>Simulated trades only. New ORB/GAP rows are exploratory SHADOW monitoring and are excluded from formal paper-book/champion allocation; historical live rows remain in the ledger. Open P&L uses the last received price and excludes exit spread, slippage and charges. Realised net includes the engine’s modelled trading costs, before income tax.<br>Quote polls: {polls}. A stale or unavailable feed is not a zero P&L.</footer></main>
<script>const el=document.getElementById('freshness');const stamp=el.dataset.stamp;function age(){{if(!stamp)return;const seconds=Math.max(0,Math.floor((Date.now()-Date.parse(stamp))/1000));const quote=el.dataset.quote;el.textContent=(seconds>90?'STALE — ':'')+'Snapshot age: '+seconds+'s'+(quote?' · Last successful quote poll: '+new Date(quote).toLocaleTimeString('en-IN',{{timeZone:'Asia/Kolkata'}})+' IST':' · No successful quote poll yet');}}age();setInterval(age,1000);</script></html>'''


def publish(ledger, engine=None, **kwargs):
    """Viewer failures must never change fills or stop the engine."""
    try:
        path = view_path(Path(ledger))
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(f".{os.getpid()}.tmp")
        temp.write_text(render(engine, **kwargs), encoding="utf-8")
        temp.replace(path)
    except Exception:
        logging.getLogger(__name__).warning("Paper viewer snapshot unavailable", exc_info=True)
