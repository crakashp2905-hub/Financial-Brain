"""``fb portfolio`` - exposures, risk decomposition, stress, limits and the gate."""
from __future__ import annotations

from .. import commands as c
from ..services import portfolio


def register(sub) -> str:
    g = sub.add_parser("portfolio", help="exposures, risk, stress, limits, the gate")
    g.add_argument("action",
                   choices=["book", "risk", "stress", "limits", "gate", "overlap"])
    g.add_argument("--as-of", required=True)
    g.add_argument("--window-days", type=int, default=400,
                   help="positions entered within N days")
    g.add_argument("--open-only", action="store_true",
                   help="the open book rather than a window")
    g.add_argument("--capital-inr", type=float, default=1e7)
    g.add_argument("--isin")
    g.add_argument("--weight", type=float)
    g.add_argument("--adv-inr", type=float)
    g.add_argument("--no-stress", action="store_true")
    g.add_argument("--a", help="first signal, for `overlap`")
    g.add_argument("--b", help="second signal, for `overlap`")
    g.add_argument("--horizon", type=int, default=20)
    c.add_json(g)
    g.set_defaults(fn=run)
    return "portfolio"


def _book(r) -> None:
    print(f"as of {r['as_of']}   {r['positions']} positions   "
          f"gross weight {r['gross_weight']:.2f}")
    print(f"illiquid (small+micro)  {r['illiquid_share']:.1%}")
    print(f"unclassified by group   {r['unclassified_group_share']:.1%}")
    if not r["industry_available"]:
        print("industry exposure       not available: this archive holds no sector "
              "classification")
    print("\nby liquidity tier")
    c._print_rows([{"tier": k, "share": f"{v:.1%}"} for k, v in r["by_tier"].items()])
    print("\nby promoter group")
    c._print_rows([{"group": str(k)[:44], "share": f"{v:.1%}"}
                   for k, v in list(r["by_group"].items())[:10]])
    print("\nfactor tilts (0.5 = the universe)")
    c._print_rows([{"factor": k, "percentile": f"{v:.3f}"}
                   for k, v in r["factor_tilts"].items() if not k.startswith("_")])


def _risk(r) -> None:
    q = r["covariance_quality"]
    print(f"as of {r['as_of']}")
    print(f"  portfolio vol (annual)   {r['portfolio_vol_annual']:.1%}")
    print(f"  effective bets           {r['effective_bets']:.2f}  "
          f"(entropy {r['effective_bets_entropy']:.2f})")
    print(f"  effective positions      {r['effective_positions']:.2f}")
    print(f"  first factor share       {r['first_factor_share']:.1%}")
    print(f"  diversification ratio    {r['diversification_ratio']:.2f}")
    print(f"  concentrated             {r['concentrated']}")
    print(f"\n  covariance: {q['assets']} assets, {q['observations']} obs "
          f"({q['obs_per_asset']:.1f}/asset), shrinkage {q['shrinkage_delta']:.3f} "
          f"({q['shrinkage_method']})")
    print(f"  average correlation {q['average_correlation']:+.3f}   "
          f"trustworthy {q['trustworthy']}")
    if not q["trustworthy"]:
        print("  WARNING: under-determined - the numbers above are properties of the "
              "shrinkage target rather than measurements")
    print("\ntop risk contributors")
    c._print_rows([{"name": p.get("ticker") or p["name"], "weight": f"{p['weight']:.1%}",
                    "risk share": f"{p['risk_share']:.1%}",
                    "amplification": f"{p['amplification']:.2f}x",
                    "beta to book": f"{p['beta_to_book']:.2f}"}
                   for p in r["positions"][:12]])
    print("\nprincipal components")
    for pc in r["principal_components"]:
        load = ", ".join(f"{n} {v:+.2f}" for n, v in pc["top_loadings"][:4])
        print(f"  PC{pc['index']}  {pc['share']:.1%}   {load}")


def _stress(r) -> None:
    print(f"as of {r['as_of']}   {r['names']} names   {r['sessions']} sessions   "
          f"min coverage {r['min_coverage']:.0%}")
    print("\nworst historical windows, from the book's own replayed returns")
    rows = []
    for h, v in r["windows"].items():
        if "note" in v:
            continue
        rows.append({"sessions": h, "n": v["n"], "vol": f"{v['vol']:.2%}",
                     "VaR95": f"{v['var']:.2%}",
                     "ES95": f"{v['expected_shortfall']:.2%}",
                     "worst": f"{v['worst'][0]['return']:.2%}",
                     "when": f"{v['worst'][0]['start']}..{v['worst'][0]['end']}"})
    c._print_rows(rows)
    cv = r["conditional_vol"]
    print(f"\nvolatility conditional on India VIX  (overall "
          f"{cv['overall_vol_annual']:.1%})")
    for k, v in (cv.get("by_vix") or {}).items():
        print(f"  {k:9s} n {v['n']:4d}  VIX {v['vix_range'][0]:5.1f}-"
              f"{v['vix_range'][1]:5.1f}  vol {v['vol_annual']:6.1%}")
    if "vix_vol_multiplier" in cv:
        print(f"  high/low multiplier {cv['vix_vol_multiplier']:.2f}x")
    print("\nby regime")
    c._print_rows([{"regime": k, "n": v["n"], "vol": f"{v['vol_annual']:.1%}",
                    "mean/day": f"{v['mean_daily']:+.3%}"}
                   for k, v in (cv.get("by_regime") or {}).items()])
    cl = r["conditional_liquidity"]
    if "turnover_ratio_median" in cl:
        print(f"\ncrisis liquidity: {cl['crisis_sessions']} sessions with the index at or "
              f"below {cl['crisis_threshold_return']:.2%}")
        print(f"  turnover ratio  median {cl['turnover_ratio_median']:.2f}  "
              f"p10 {cl['turnover_ratio_p10']:.2f}  p90 {cl['turnover_ratio_p90']:.2f}")
        print(f"  turnover FALLS in a crisis for "
              f"{cl['names_where_turnover_falls']}/{cl['names']} names")
        print(f"  caveat: {cl['caveat']}")


def _limits(r) -> None:
    print(f"as of {r['as_of']}   verdict {r['verdict']}")
    if not r["structure_limits_applied"]:
        print(f"  structure limits NOT applied: {r['structure_not_applied_because']}")
    if not r["breaches"]:
        print("  no limit breached")
        return
    for b in r["breaches"]:
        kind = "DATA GAP" if b["kind"] == "data" else "LIMIT"
        cap = (f"   resize to <= {b['max_passing_weight']:.2%}"
               if b["max_passing_weight"] else "   no size passes")
        print(f"  [{kind}] {b['check']}  measured {b['measured']:.4f} "
              f"vs limit {b['limit']}{cap}")
        print(f"      {b['why']}")


def _gate(r) -> None:
    print(f"{r['verdict']}   requested {r['requested_weight']:.2%}   "
          f"approved {r['approved_weight']:.2%}")
    for reason in r["reasons"]:
        print(f"  - {reason}")
    for b in r["breaches"]:
        print(f"  [{b.get('kind', 'limit')}] {b['check']}: {b['why']}")


def _overlap(r) -> None:
    print(f"{r['a']} vs {r['b']} at h={r['horizon']} over {r['rebalances']} rebalances")
    print(f"  holdings overlap   mean {r['mean_overlap']:.1%}  "
          f"min {r['min_overlap']:.1%}  max {r['max_overlap']:.1%}")
    cr = r["excess_correlation"]
    print(f"  excess correlation {cr:+.3f}" if cr is not None
          else "  excess correlation  -")
    print(f"  same book          {r['same_book']}")


@c.guard
def run(args) -> int:
    window = None if args.open_only else args.window_days
    as_of = c.parse_date(args.as_of)
    with c.connect() as con:
        if args.action == "book":
            return c.emit(portfolio.book(con, as_of=as_of, window_days=window),
                          args, _book)
        if args.action == "risk":
            return c.emit(portfolio.risk(con, as_of=as_of, window_days=window),
                          args, _risk)
        if args.action == "stress":
            return c.emit(portfolio.stress_test(con, as_of=as_of, window_days=window,
                                                capital_inr=args.capital_inr),
                          args, _stress)
        if args.action == "limits":
            return c.emit(portfolio.check_limits(con, as_of=as_of, window_days=window),
                          args, _limits)
        if args.action == "overlap":
            if not (args.a and args.b):
                print("bad_request: --a and --b are required for `overlap`")
                return c.BAD
            return c.emit(portfolio.signal_overlap(con, a=args.a, b=args.b,
                                                   horizon=args.horizon),
                          args, _overlap)
        if not (args.isin and args.weight):
            print("bad_request: --isin and --weight are required for `gate`")
            return c.BAD
        return c.emit(portfolio.gate(con, isin=args.isin, weight=args.weight,
                                     as_of=as_of, capital_inr=args.capital_inr,
                                     adv_inr=args.adv_inr, window_days=window,
                                     with_stress=not args.no_stress),
                      args, _gate)
