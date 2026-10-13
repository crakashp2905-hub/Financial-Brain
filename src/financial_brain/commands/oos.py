"""``fb oos`` - G5 of ADR-0004: each registered trial's live paper record against its backtest's band, and the
intraday runner's measured fills against its cost model."""
from __future__ import annotations

import duckdb

from .. import commands as c
from ..evaluation import oos_monitor as OM


def register(sub) -> str:
    g = sub.add_parser("oos", help="out-of-sample monitor: live paper vs the backtest band (ADR-0004 G5)")
    g.add_argument("--zoo", default="data/paper_zoo.duckdb")
    g.add_argument("--live", default="data/paper_live.duckdb")
    c.add_json(g)
    g.set_defaults(fn=run)
    return "oos"


def _show(r) -> None:
    print(OM.render(r["trials"]))
    d = r["execution"]
    if d.get("trades"):
        print(f"\nintraday fills ({d['trades']} live trades)")
        c._print_rows([{k: (f"{v:.1f}" if isinstance(v, float) else v) for k, v in x.items()} for x in d["by_strategy"]])


@c.guard
def run(args) -> int:
    z = duckdb.connect(args.zoo, read_only=True)
    try:
        trials = OM.report(z)
    finally:
        z.close()
    try:
        lc = duckdb.connect(args.live, read_only=True)
        try:
            ex = OM.execution_drift(lc)
        finally:
            lc.close()
    except duckdb.Error as e:                       # the live runner holds it during the session
        ex = {"trades": 0, "note": str(e)}
    return c.emit({"trials": trials, "execution": ex}, args, _show)
