"""``fb trials`` - the trial ledger, the Bonferroni bar, and what has been tried.

Named ``trials`` and not ``research`` because ``fb research`` already means the published-anomaly
catalogue - 103 anomalies from 99 papers, and what of them this archive can reach. That is the
question "what could be tested"; this is "what has been, and what did it cost". Two different
things that would have collided under one name.
"""
from __future__ import annotations

from .. import commands as c
from ..services import research


def register(sub) -> str:
    g = sub.add_parser("trials",
                       help="the trial ledger, the Bonferroni bar, screened conditions")
    g.add_argument("action", choices=["bar", "tried", "ledger", "conditions"])
    g.add_argument("--feature")
    g.add_argument("--limit", type=int, default=25)
    c.add_json(g)
    g.set_defaults(fn=run)
    return "trials"


def _bar(r) -> None:
    print(f"trials recorded    {r['trials_recorded']}")
    print(f"counting a new one {r['counting_this_one']}")
    print(f"bar                |t| > {r['bar']:.2f}")
    print(f"basis              {r['basis']}")


def _tried(r) -> None:
    t = r["totals"]
    print(f"{t['features']} features, {t['trials']} trials, "
          f"{t['currently_promoted']} currently promoted, "
          f"{t['stale_promotes']} stale")
    print(f"bar |t| > {r['bar']['bar']:.2f}   "
          f"cost-model epoch {r['cost_model_epoch']}")
    print(f"  {r['epoch_note']}")
    rows = [x for x in r["features"] if x["trials"] > 1 or x["ever_promoted"]]
    c._print_rows([{"feature": x["feature"], "trials": x["trials"],
                    "best |ic_t|": (f"{x['best_abs_ic_t']:.2f}"
                                    if x["best_abs_ic_t"] is not None else "-"),
                    "verdict": x["current_verdict"],
                    "stale": "yes" if x["promote_is_stale"] else "",
                    "last": x["last_tested"]} for x in rows], limit=30)


def _ledger(r) -> None:
    print(f"bar |t| > {r['bar']['bar']:.2f} at {r['bar']['trials_recorded']} trials")
    print(f"  {r['note']}")
    c._print_rows([{"feature": x["feature"], "h": x["horizon"], "n": x["rebalances"],
                    "ic_t": (f"{x['ic_t']:+.2f}" if x["ic_t"] is not None else "-"),
                    "sharpe": (f"{x['net_sharpe']:+.3f}"
                               if x["net_sharpe"] is not None else "-"),
                    "verdict": x["verdict"], "run": x["run_at"]}
                   for x in r["trials"]], limit=40)


def _conditions(rows) -> None:
    for x in rows:
        pr = x["prior"]
        mark = "PASSES" if pr.get("clears_bar") else "rejected"
        print(f"{x['key']:28s} dir {x['direction']:+d}  [{mark}]")
        print(f"    {x['claim']}")
        print(f"    source: {x['source'][:88]}")
        print(f"    prior:  {pr.get('summary', 'none')[:150]}")
        if x["caveat"]:
            print(f"    CAVEAT: {x['caveat'][:150]}")


@c.guard
def run(args) -> int:
    with c.connect() as con:
        if args.action == "bar":
            return c.emit(research.bar(con), args, _bar)
        if args.action == "tried":
            return c.emit(research.what_has_been_tried(con), args, _tried)
        if args.action == "conditions":
            return c.emit(research.registered_conditions(con), args, _conditions)
        return c.emit(research.ledger(con, feature=args.feature, limit=args.limit),
                      args, _ledger)
