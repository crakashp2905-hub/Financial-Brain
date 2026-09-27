"""``fb opportunities`` - what is unusual today, with each condition's measured record."""
from __future__ import annotations

from .. import commands as c
from ..services import research


def register(sub) -> str:
    g = sub.add_parser("opportunities",
                       help="names satisfying a measured condition, ranked by evidence")
    g.add_argument("--as-of", required=True)
    g.add_argument("--key", action="append", help="restrict to one condition (repeatable)")
    g.add_argument("--per-condition", type=int, default=6)
    c.add_json(g)
    g.set_defaults(fn=run)
    return "opportunities"


def _show(r) -> None:
    b = r["bonferroni"]
    print(f"as of {r['as_of']}   features {r['features_date']}   "
          f"universe {r['universe']}   bar |t| > {b['bar']:.2f} at {b['trials']} trials")

    if r["skipped_conditions"]:
        print("\nnot emitted")
        for s in r["skipped_conditions"]:
            print(f"  {s['condition']:28s} {s['why']}")

    print(f"\n{len(r['opportunities'])} opportunities, "
          f"{r['clearing_the_bar']} clearing the bar")
    seen = set()
    for o in r["opportunities"]:
        first = o["condition"] not in seen
        seen.add(o["condition"])
        if first:
            print()
            pr = o["prior"]
            mark = "PASSES" if pr.get("clears_bar") else "rejected"
            print(f"  {o['condition']}  [{mark}]  dir {o['direction']:+d}")
            print(f"    {o['claim']}")
            print(f"    prior: {pr.get('summary', '')[:160]}")
            if o["caveat"]:
                print(f"    CAVEAT: {o['caveat'][:160]}")
            print("    names:", end=" ")
        print(o["ticker"] or o["isin"], end="  ")
    print()
    if not r["clearing_the_bar"]:
        # The honest headline. An engine that emits candidates without this is a random
        # number generator with a vocabulary.
        print("\nNothing here has a condition that cleared the firewall. These are states "
              "worth a human eye, not signals with a measured edge.")


@c.guard
def run(args) -> int:
    with c.connect() as con:
        return c.emit(research.scan(con, as_of=c.parse_date(args.as_of), keys=args.key,
                                    limit_per_condition=args.per_condition),
                      args, _show)
