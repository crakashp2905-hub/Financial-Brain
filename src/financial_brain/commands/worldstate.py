"""``fb world`` - what the system knew as of one session."""
from __future__ import annotations

from .. import commands as c
from ..services import market


def register(sub) -> str:
    g = sub.add_parser("world", help="market state, indices and regime history")
    g.add_argument("action", choices=["state", "indices", "regimes"])
    g.add_argument("--as-of", help="YYYY-MM-DD (default: latest priced session)")
    g.add_argument("--start")
    g.add_argument("--end")
    g.add_argument("--name", action="append", help="index name (repeatable)")
    g.add_argument("--version", help="regime classifier version (default: latest)")
    g.add_argument("--limit", type=int, default=40)
    c.add_json(g)
    g.set_defaults(fn=run)
    return "world"


def _state(r) -> None:
    print(f"session            {r['session']}")
    # The classifier version is printed because market_regime is append-only and v1/v2 disagree
    # on 425 of 2,894 sessions - "RISK_OFF" alone does not identify an answer here.
    print(f"regime             {r['regime']['value']}  ({r['regime']['version']})")
    if r["india_vix"]:
        print(f"india vix          {r['india_vix']['value']:.2f}  as of "
              f"{r['india_vix']['as_of']}")
    b = r["breadth"]
    ad = f"{b['advance_decline']:.2f}" if b["advance_decline"] else "-"
    print(f"breadth            {b['advancing']} up / {b['declining']} down "
          f"of {b['priced']} priced   A/D {ad}")
    print(f"universe           {r['universe_names']} names")
    n = r["announcements_3d"]
    print(f"announcements 3d   {n['count']}  latest {n['latest']}")


def _indices(rows) -> None:
    c._print_rows([{"index": x["index"], "as_of": x["as_of"],
                    "level": f"{x['level']:,.2f}",
                    "change": (f"{x['change']:+.2%}" if x["change"] is not None else "-")}
                   for x in rows])


def _regimes(r) -> None:
    print(f"version {r['version']}   {r['sessions']} sessions")
    c._print_rows([{"regime": x["regime"], "sessions": x["sessions"],
                    "share": f"{x['share']:.1%}", "first": x["first"], "last": x["last"]}
                   for x in r["regimes"]])


@c.guard
def run(args) -> int:
    with c.connect() as con:
        if args.action == "state":
            return c.emit(market.world_state(con, as_of=c.parse_date(args.as_of)),
                          args, _state)
        if args.action == "indices":
            return c.emit(market.indices(con, names=args.name,
                                         as_of=c.parse_date(args.as_of),
                                         limit=args.limit), args, _indices)
        return c.emit(market.regime_history(con, start=c.parse_date(args.start),
                                           end=c.parse_date(args.end),
                                           version=args.version), args, _regimes)
