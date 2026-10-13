"""``fb live`` - the order management system's controls (ADR-0005). Nothing here enables real orders by itself.

    status                  stage, kill switch, each strategy's verdict and closed locks, orders today
    rehearse                one session of the OMS against PaperBroker on live Kite quotes (counts toward stage 1)
    promote --to N --by X   raise the stage one step, if every criterion is met (a human only)
    approve ID --by X       release an order waiting at stage TICKET (a human only)
    decline ID --by X
    kill --reason R         trip the broker kill switch (anyone may stop; cancels open orders at the broker)
    reset --by X --note N   clear it (a human only)
    verify                  walk the journal's hash chain
"""
from __future__ import annotations

import json

from .. import commands as c
from ..services import trading


def register(sub) -> str:
    g = sub.add_parser("live", help="order management: stages, approvals, kill switch, rehearsal (ADR-0005)")
    g.add_argument("action", choices=["status", "rehearse", "promote", "approve", "decline", "kill", "reset", "verify"])
    g.add_argument("order_id", nargs="?")
    g.add_argument("--to", type=int)
    g.add_argument("--by", help="your name: approvals, promotions and resets refuse agent: actors")
    g.add_argument("--reason", default="manual")
    g.add_argument("--note", default="")
    g.add_argument("--strategy", default="edge_m1_momentum_book")
    g.add_argument("--runbook-read", action="store_true", help="you have read docs/RUNBOOK-LIVE.md")
    c.add_json(g)
    g.set_defaults(fn=run)
    return "live"


def _show_status(r) -> None:
    print(f"stage        {r['stage']} (since {r['since']})")
    ks = r["kill_switch"]
    print(f"kill switch  {'TRIPPED: ' + str(ks.get('reason')) if ks.get('tripped') else 'clear'}")
    jr = r["journal"]
    print(f"journal      {'chain ok' if jr['ok'] else 'BROKEN at ' + str(jr.get('broken_at'))} ({jr.get('events', 0)} events)")
    for s, closed in r["locks_closed"].items():
        print(f"\n{s} [{r['verdicts'][s]}]: " + ("every lock open" if not closed else f"{len(closed)} lock(s) closed"))
        for x in closed:
            print(f"   - {x}")
    if r["awaiting_approval"]:
        print("\nawaiting approval: " + ", ".join(r["awaiting_approval"]))


def _dump(r) -> None:
    print(json.dumps(r, indent=1, default=str))


@c.guard
def run(args) -> int:
    a = args.action
    if a == "status":
        return c.emit(trading.status(), args, _show_status)
    if a == "rehearse":
        return c.emit(trading.rehearse(args.strategy), args, _dump)
    if a == "verify":
        return c.emit(trading.verify(), args, _dump)
    if a == "kill":
        return c.emit(trading.kill(args.reason, args.strategy), args, _dump)
    if not args.by:
        print("--by <your name> is required")
        return c.BAD
    try:
        if a == "reset":
            return c.emit(trading.reset(args.by, args.note), args, _dump)
        if a == "promote":
            return c.emit(trading.promote(args.to, args.by, args.strategy, args.runbook_read), args, _dump)
        return c.emit(trading.ticket(a, args.order_id, args.by, args.strategy), args, _dump)
    except PermissionError as e:
        print(f"refused: {e}")
        return c.REFUSED
