"""``fb decide`` - read decisions, compile one from typed claims, challenge a thesis.

The compile path takes claims from a JSON file rather than from flags. Three or four sources each
with a direction, a strength and evidence ids is not a command line, and flattening it into one
would invite the shape of input the compiler exists to refuse.
"""
from __future__ import annotations

import json
from pathlib import Path

from .. import commands as c
from ..services import decisions


def register(sub) -> str:
    g = sub.add_parser("decide", help="decisions: list, show, compile, challenge, quality")
    g.add_argument("action", choices=["list", "show", "compile", "challenge", "quality"])
    g.add_argument("decision_id", nargs="?")
    g.add_argument("--state")
    g.add_argument("--action-filter", dest="action_filter")
    g.add_argument("--limit", type=int, default=25)
    g.add_argument("--input", help="JSON file of claims/scenarios/prices, for `compile`")
    g.add_argument("--mechanism", help="for `challenge`")
    g.add_argument("--observable", help="for `challenge`")
    g.add_argument("--evidence", action="append", default=[], help="evidence id (repeatable)")
    g.add_argument("--probability", type=float, default=0.0)
    c.add_json(g)
    g.set_defaults(fn=run)
    return "decide"


def _list(rows) -> None:
    c._print_rows([{"decision_id": r["decision_id"][:20], "isin": r["isin"],
                    "action": r["action"], "no-trade": "yes" if r["is_no_trade"] else "",
                    "state": r["state"], "author": r["author"],
                    "created": str(r["created_at"])[:10]} for r in rows])


def _show(r) -> None:
    cont = r["content"]
    print(f"{r['decision_id']}   {cont.get('action')}   state {r['state']}"
          f"{'   (no trade)' if r['is_no_trade'] else ''}")
    print(f"  isin      {cont.get('isin')}")
    print(f"  horizon   {cont.get('horizon_days')} days")
    print(f"  thesis    {(cont.get('thesis') or '')[:150]}")
    print(f"  primary uncertainty  {(cont.get('primary_uncertainty') or '')[:120]}")
    for cond in (cont.get("invalidation_conditions") or [])[:4]:
        print(f"  invalidation  {cond[:110]}")
    if r["history"]:
        print("  history")
        for h in r["history"]:
            print(f"    {h['to_state']:22s} {h.get('actor', '')}  "
                  f"{str(h.get('event_at', ''))[:19]}")
    att = r["adversary_attempts"]
    if att:
        print(f"  adversary: {len(att)} attempt(s), "
              f"{sum(1 for a in att if a['passed'])} passed")
        for a in att:
            print(f"    #{a['seq']} {'PASS' if a['passed'] else 'FAIL'}  "
                  f"{a['mechanism'][:90]}")
            for f in a["failures"]:
                print(f"        - {f[:100]}")


def _compiled(r) -> None:
    print(f"{r['action']}   decided by rule {r['decided_by']}   "
          f"weight {r['weight']:.2%}")
    for reason in r["reasons"]:
        print(f"  - {reason}")
    if r["contradictions"]:
        print("  contradictions")
        for x in r["contradictions"]:
            print(f"    - {x}")
    if r["numbers"]:
        print("  numbers")
        for k, v in r["numbers"].items():
            if isinstance(v, float):
                print(f"    {k:24s} {v:+.4f}")
            else:
                print(f"    {k:24s} {v}")
    cl = r["inputs"]["claims"]
    print(f"  claims: {cl['n']} sources, {cl['bulls']} bullish, {cl['bears']} bearish, "
          f"disagreement {cl['disagreement']:.2f}")


def _challenge(r) -> None:
    print(f"{r['decision_id']}   {'ACCEPTED' if r['passed'] else 'REJECTED'}   "
          f"attempt {r['attempts']}")
    for f in r["failures"]:
        print(f"  - {f}")
    m = r["measured"]
    print(f"  words {m['words']}  generic {m['generic_share']:.0%}  "
          f"falsifiable {m['falsifiable']}  restatement {m['restatement_overlap']:.0%}  "
          f"evidence {m['evidence_count']}")


def _quality(r) -> None:
    print(f"{r['decision_id']}   score {r['score']}")
    print(f"  {r['note']}")
    for k, v in (r["components"] or {}).items():
        print(f"  {k:20s} {v}")


@c.guard
def run(args) -> int:
    needs_id = args.action in ("show", "challenge", "quality")
    if needs_id and not args.decision_id:
        print(f"bad_request: `{args.action}` needs a decision_id")
        return c.BAD

    # `challenge` records an attempt, so it is the one action here that writes.
    with c.connect(write=args.action == "challenge") as con:
        if args.action == "list":
            return c.emit(decisions.listing(con, state=args.state,
                                            action=args.action_filter,
                                            limit=args.limit), args, _list)
        if args.action == "show":
            return c.emit(decisions.get(con, args.decision_id), args, _show)
        if args.action == "quality":
            return c.emit(decisions.score_quality(con, args.decision_id), args, _quality)
        if args.action == "challenge":
            if not (args.mechanism and args.observable):
                print("bad_request: --mechanism and --observable are required")
                return c.BAD
            return c.emit(decisions.challenge(con, decision_id=args.decision_id,
                                              mechanism=args.mechanism,
                                              observable=args.observable,
                                              evidence=args.evidence,
                                              probability=args.probability),
                          args, _challenge)
        if not args.input:
            print("bad_request: `compile` needs --input pointing at a JSON file with "
                  "claims, scenarios, entry, invalidation and capital_inr")
            return c.BAD
        spec = json.loads(Path(args.input).read_text(encoding="utf-8"))
        return c.emit(decisions.compile_one(
            con, isin=spec["isin"], as_of=c.parse_date(spec["as_of"]),
            claims=spec["claims"], scenarios=spec.get("scenarios"),
            entry=spec.get("entry"), invalidation=spec.get("invalidation"),
            capital_inr=spec["capital_inr"],
            horizon_days=spec.get("horizon_days", 90)), args, _compiled)
