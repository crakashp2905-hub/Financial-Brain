"""``fb company`` - identity, overview, peers, and why a session moved."""
from __future__ import annotations

from .. import commands as c
from ..services import companies


def register(sub) -> str:
    g = sub.add_parser("company", help="one name: overview, peers, why it moved")
    g.add_argument("action", choices=["find", "show", "why", "peers"])
    g.add_argument("query", help="ISIN, ticker or name fragment (ISIN for show/why/peers)")
    g.add_argument("--as-of")
    g.add_argument("--on", help="the session to explain, for `why`")
    g.add_argument("--index", default="Nifty 500")
    g.add_argument("--k", type=int, default=12)
    c.add_json(g)
    g.set_defaults(fn=run)
    return "company"


def _find(rows) -> None:
    c._print_rows([{"isin": r["isin"], "ticker": r["ticker"],
                    "company": (r["company_name"] or "")[:44],
                    "first": r["first_seen"], "last": r["last_seen"]} for r in rows])


def _show(r) -> None:
    i, p, f = r["identity"], r["price"], r["features"]
    print(f"{i['ticker']}  {i['company_name']}")
    print(f"  isin             {r['isin']}")
    print(f"  listed           {i['listing_date']}")
    print(f"  promoter group   {i['promoter_group'] or '(not identified)'}")
    # Stated, not omitted: there is no industry classification in this archive.
    print("  industry         (not available in this archive)")
    print(f"  close            {p['close_adj']:,.2f}  as of {p['as_of']}")
    print(f"  turnover         {p['turnover']:,.0f}")
    if f:
        print(f"  features as of   {f['as_of']}")
        for key, fmt in (("mom_12_1", "+.4f"), ("dist_52w_high", "+.4f"),
                         ("vol_60", ".4f"), ("ret_20d", "+.4f"), ("rsi_14", ".1f"),
                         ("adv20", ",.0f")):
            if f.get(key) is not None:
                print(f"    {key:14s} {f[key]:{fmt}}")
    if r["recent_events"]:
        print("  recent events")
        for e in r["recent_events"][:5]:
            print(f"    {e['date']}  [{e['event_type']}/{e['materiality']}]  "
                  f"{e['headline'][:70]}")


def _why(r) -> None:
    print(f"{r['isin']} on {r['date']}:  {r['move']:+.2%}")
    for part in r["components"]:
        print(f"  {part['part']:9s} {part['value']:+8.2%}   {part['detail']}")
    print(f"  {'':9s} {'':8s}   sums to the move: {r['sums_to_move']}")
    f = r["fit"]
    print(f"  fitted on {f['sessions']} sessions ending {f['ends']}, "
          f"residual sd {f['residual_sd']:.2%}")
    if not r["explained"]:
        print("  NOT EXPLAINED - this move is a data artifact, not a market event:")
        for flag in r["data_quality_flags"]:
            print(f"    - {flag}")
    n = r.get("candidate_news")
    if n and n["found"]:
        print(f"  candidate news ({n['caveat']}):")
        for it in n["items"][:4]:
            print(f"    {it['date']}  [{it['event_type']}]  {it['headline'][:70]}")


def _peers(r) -> None:
    print(f"{r['isin']} peers as of {r['as_of']} - basis: {r['basis']}")
    print("  (an empirical group by co-movement; NOT a sector classification)")
    c._print_rows([{"ticker": p["ticker"], "isin": p["isin"],
                    "correlation": f"{p['correlation']:+.3f}",
                    "sessions": p["sessions"]} for p in r["peers"]])


@c.guard
def run(args) -> int:
    with c.connect() as con:
        if args.action == "find":
            return c.emit(companies.resolve(con, args.query), args, _find)
        if args.action == "show":
            return c.emit(companies.overview(con, args.query,
                                             as_of=c.parse_date(args.as_of)), args, _show)
        if args.action == "why":
            if not args.on:
                print("bad_request: --on YYYY-MM-DD is required for `why`")
                return c.BAD
            return c.emit(companies.why_did_it_move(con, args.query,
                                                    on=c.parse_date(args.on),
                                                    index_name=args.index), args, _why)
        as_of = c.parse_date(args.as_of)
        if not as_of:
            print("bad_request: --as-of YYYY-MM-DD is required for `peers`")
            return c.BAD
        return c.emit(companies.peer_group(con, args.query, as_of=as_of, k=args.k),
                      args, _peers)
