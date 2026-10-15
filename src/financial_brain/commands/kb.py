"""``fb kb`` - the knowledge base: every signal, indicator, rule, anomaly and strategy, with every test it was in."""
from __future__ import annotations

from .. import commands as c
from ..services import knowledge


def register(sub) -> str:
    g = sub.add_parser("kb", help="knowledge base: evidence cards for every signal, rule and strategy")
    g.add_argument("action", choices=["build", "search", "card"])
    g.add_argument("query", nargs="?", default="")
    g.add_argument("--limit", type=int, default=25)
    c.add_json(g)
    g.set_defaults(fn=run)
    return "kb"


def _show(r) -> None:
    if isinstance(r, list):
        c._print_rows(r, limit=60)
    elif "card" in r:
        print(r["card"])
        c._print_rows([{k: x[k] for k in ("origin", "horizon", "stat", "value_a", "value_b", "verdict", "tested_on")}
                       for x in r["tests"]], limit=60)
    else:
        for k, v in r.items():
            print(f"{k:12s} {v}")


@c.guard
def run(args) -> int:
    if args.action == "build":
        with c.connect() as con:
            return c.emit(knowledge.build(con), args, _show)
    if args.action == "search":
        return c.emit(knowledge.search(args.query, args.limit), args, _show)
    return c.emit(knowledge.card(args.query), args, _show)
