"""``fb oos`` - G5 of ADR-0004: each registered trial's live paper record against its backtest's band, the intraday
runner's measured fills against its cost model, and meta-label readiness."""
from __future__ import annotations

from .. import commands as c
from ..services import trading


def register(sub) -> str:
    g = sub.add_parser("oos", help="out-of-sample monitor: live paper vs the backtest band (ADR-0004 G5)")
    g.add_argument("--zoo", default=str(trading.ZOO))
    g.add_argument("--live", default=str(trading.LIVE_LEDGER))
    c.add_json(g)
    g.set_defaults(fn=run)
    return "oos"


def _show(r) -> None:
    print(trading.render_trials(r["trials"]))
    d = r["execution"]
    if d.get("trades"):
        print(f"\nintraday fills ({d['trades']} live trades)")
        c._print_rows([{k: (f"{v:.1f}" if isinstance(v, float) else v) for k, v in x.items()} for x in d["by_strategy"]])
    ev = r["metalabel"]["evidence"]
    if "trades" in ev:
        print(f"\nmeta-label: {ev['trades']} closed live trades over {ev['days']} sessions"
              + ("" if ev["ok"] else f" - not fitted until {ev['need']}"))
    fit = r["metalabel"].get("fit")
    if fit:
        print(f"  {fit['groups_examined']} context groups examined; flagged: "
              + (", ".join(f"{g['context']}={g['value']}" for g in fit["flagged"]) or "none")
              + f"; walk-forward: {fit['walk_forward'].get('verdict')}")


@c.guard
def run(args) -> int:
    return c.emit(trading.oos(args.zoo, args.live), args, _show)
