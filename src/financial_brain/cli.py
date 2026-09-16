"""``fb`` - Financial-Brain command line.

Phase 0 surface: migrate, ingest, replay, inspect. Deliberately small - these are
idempotent jobs a scheduler can call, not an application.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta

from .config import load
from .corpactions import actions as ca
from .costs.india import DEFAULT as COSTS, Segment
from .ingest.job import BhavcopyIngestJob
from .pit.observations import PITStore, close_price_observations
from .securities import master as sec
from .storage.db import Database
from .universe import snapshot as uni


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _print_table(rows: list[dict], limit: int = 40) -> None:
    if not rows:
        print("  (no rows)")
        return
    cols = list(rows[0].keys())
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows[:limit])) for c in cols}
    print("  " + "  ".join(c.ljust(widths[c]) for c in cols))
    print("  " + "  ".join("-" * widths[c] for c in cols))
    for r in rows[:limit]:
        print("  " + "  ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols))
    if len(rows) > limit:
        print(f"  ... {len(rows) - limit} more")


# --------------------------------------------------------------------- commands
def cmd_migrate(args) -> int:
    cfg = load()
    Database(cfg).migrate()
    print(f"schema ready at {cfg.db_path}")
    print(f"  lake       {cfg.lake}")
    print(f"  curated    {cfg.curated}")
    print(f"  quarantine {cfg.quarantine}")
    return 0


def cmd_ingest(args) -> int:
    start = _d(args.start)
    end = _d(args.end) if args.end else start
    sources = args.source or ["NSE", "BSE"]
    Database(load()).migrate()

    counts: dict[str, int] = {}
    failures = []
    for src in sources:
        for res in BhavcopyIngestJob(src).run_range(
                start, end, force=args.force, from_lake=args.from_lake):
            counts[res.status] = counts.get(res.status, 0) + 1
            if not res.ok or args.verbose:
                print(res)
            if not res.ok:
                failures.append(res)

    print("\nsummary: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if failures:
        print(f"{len(failures)} run(s) need attention - see `fb runs --failed`")
    return 1 if failures and args.strict else 0


def cmd_runs(args) -> int:
    db = Database(load())
    sql = """SELECT business_date, source, status, rows_out, message, started_at
             FROM ingest_runs"""
    if args.failed:
        sql += " WHERE status NOT IN ('ok','skipped','not_published')"
    sql += " ORDER BY started_at DESC LIMIT ?"
    _print_table(db.query_dicts(sql, [args.limit]), limit=args.limit)
    return 0


def cmd_dq(args) -> int:
    db = Database(load())
    rows = db.query_dicts("""
        SELECT r.business_date, r.source, d.check_name, d.severity, d.passed,
               d.observed, d.detail
        FROM dq_results d JOIN ingest_runs r USING (run_id)
        WHERE NOT d.passed
        ORDER BY r.business_date DESC LIMIT ?""", [args.limit])
    if not rows:
        print("no failing data-quality checks")
        return 0
    _print_table(rows, limit=args.limit)
    return 0


def cmd_status(args) -> int:
    cfg = load()
    db = Database(cfg)
    db.migrate()

    lake_files = sum(1 for _ in cfg.lake.rglob("*")) if cfg.lake.exists() else 0
    lake_bytes = sum(f.stat().st_size for f in cfg.lake.rglob("*") if f.is_file())

    print("FINANCIAL-BRAIN  PHASE 0 STATUS")
    print("=" * 64)
    print(f"raw lake            {lake_files} files, {lake_bytes/1e6:.1f} MB")

    for label, sql in [
        ("lake manifest", "SELECT COUNT(*) FROM lake_manifest"),
        ("ingest runs", "SELECT COUNT(*) FROM ingest_runs"),
        ("securities (ISIN)", "SELECT COUNT(*) FROM securities"),
        ("listings", "SELECT COUNT(*) FROM security_listings"),
        ("universe rows", "SELECT COUNT(*) FROM universe_snapshots"),
        ("corporate actions", "SELECT COUNT(*) FROM corporate_actions"),
        ("PIT observations", "SELECT COUNT(*) FROM pit_observations"),
    ]:
        print(f"{label:<20}{db.query(sql)[0][0]:,}")

    rng = db.query("SELECT MIN(business_date), MAX(business_date) FROM universe_snapshots")[0]
    if rng[0]:
        days = db.query("SELECT COUNT(DISTINCT business_date) FROM universe_snapshots")[0][0]
        print(f"{'coverage':<20}{rng[0]} .. {rng[1]}  ({days} trading days)")

    failed = db.query(
        "SELECT COUNT(*) FROM ingest_runs WHERE status NOT IN ('ok','skipped','not_published')"
    )[0][0]
    print(f"{'runs needing help':<20}{failed}")
    return 0


def cmd_resolve(args) -> int:
    db = Database(load())
    with db.connect() as con:
        on = _d(args.on) if args.on else None
        _print_table(sec.resolve(con, args.identifier, on_date=on))
    return 0


def cmd_universe(args) -> int:
    db = Database(load())
    with db.connect() as con:
        if args.churn_from:
            out = uni.churn(con, _d(args.churn_from), _d(args.on), exchange=args.exchange)
            print(f"{out['exchange']}  {out['start']} -> {out['end']}")
            print(f"  at start   {out['at_start']}")
            print(f"  at end     {out['at_end']}")
            print(f"  vanished   {len(out['disappeared'])}   {out['disappeared'][:8]}")
            print(f"  appeared   {len(out['appeared'])}   {out['appeared'][:8]}")
            print("\n  Everything under 'vanished' is a name a current-universe backtest"
                  "\n  would never see. That is survivorship bias, made countable.")
            return 0
        rows = uni.as_of(con, _d(args.on), exchange=args.exchange,
                         min_turnover=args.min_turnover)
        print(f"{len(rows)} instruments tradable on {args.on}"
              f"{' on ' + args.exchange if args.exchange else ''}")
        _print_table(rows, limit=args.limit)
    return 0


def cmd_pit(args) -> int:
    db = Database(load())
    with db.connect() as con:
        store = PITStore(con)
        if args.backfill_closes:
            d = _d(args.backfill_closes)
            obs = close_price_observations(con, d, exchange=args.exchange or "NSE")
            ids = store.record_many(obs)
            print(f"recorded {len(ids)} close-price observations for {d}")
        if args.history:
            entity, attr = args.history.split(":", 1)
            _print_table(store.history(con and entity, attr))
        cov = store.coverage()
        print(json.dumps({k: str(v) for k, v in cov.items()}, indent=2))
    return 0


def cmd_costs(args) -> int:
    seg = Segment(args.segment)
    rt = COSTS.round_trip(turnover=args.turnover, segment=seg, bucket=args.bucket)
    print(f"Round trip  notional Rs {args.turnover:,.0f}  {seg.value}  bucket={args.bucket}")
    print("-" * 64)
    for leg in ("buy", "sell"):
        c = rt[leg]
        print(f"  {leg.upper():<5} " + "  ".join(
            f"{k}={v:,.2f}" for k, v in c.items()
            if k in ("stt", "exchange", "sebi", "stamp", "brokerage", "gst", "impact")))
        print(f"        total Rs {c['total']:,.2f}  ({c['bps']:.1f} bps)")
    print(f"\n  ROUND TRIP  Rs {rt['total']:,.2f}   {rt['bps']:.1f} bps")
    print(f"  breakeven move needed: {COSTS.breakeven_move(segment=seg, bucket=args.bucket)*100:.3f}%")
    print("\n  Annual drag by turnover (times/year):")
    for n in (1, 4, 12, 24, 52):
        print(f"    {n:>3}x  {COSTS.annual_drag(turnover_per_year=n, segment=seg, bucket=args.bucket)*100:6.2f}%")
    return 0


def cmd_actions(args) -> int:
    db = Database(load())
    with db.connect() as con:
        if args.rebuild:
            n = ca.rebuild_adjustment_factors(con)
            print(f"rebuilt {n} adjustment factors")
        if args.suspicious:
            rows = ca.detect_suspicious_gaps(con, threshold=args.threshold)
            print(f"{len(rows)} unexplained overnight moves >= {args.threshold:.0%} "
                  f"(candidate unrecorded corporate actions)")
            _print_table(rows, limit=args.limit)
    return 0


def cmd_check(args) -> int:
    """Phase 0 exit test: can we reconstruct a past day from cold storage?"""
    cfg = load()
    db = Database(cfg)
    d = _d(args.on)
    ok = True
    print(f"PHASE 0 EXIT TEST for {d}")
    print("=" * 64)

    with db.connect() as con:
        n_uni = con.execute(
            "SELECT COUNT(*) FROM universe_snapshots WHERE business_date = ?", [d]).fetchone()[0]
        print(f"[{'PASS' if n_uni else 'FAIL'}] universe reconstructable      {n_uni} instruments")
        ok &= bool(n_uni)

        n_isin = con.execute(
            "SELECT COUNT(DISTINCT isin) FROM universe_snapshots WHERE business_date = ?",
            [d]).fetchone()[0]
        print(f"[{'PASS' if n_isin else 'FAIL'}] ISIN identity resolvable     {n_isin} ISINs")
        ok &= bool(n_isin)

        n_ev = con.execute(
            "SELECT COUNT(*) FROM lake_manifest WHERE business_date = ?", [d]).fetchone()[0]
        print(f"[{'PASS' if n_ev else 'FAIL'}] raw payload preserved        {n_ev} lake objects")
        ok &= bool(n_ev)

        bad = con.execute("""SELECT COUNT(*) FROM dq_results d JOIN ingest_runs r USING (run_id)
                             WHERE r.business_date = ? AND NOT d.passed AND d.severity='error'""",
                          [d]).fetchone()[0]
        print(f"[{'PASS' if bad == 0 else 'FAIL'}] quality contracts held        {bad} errors")
        ok &= bad == 0

    print("=" * 64)
    print("PASS - the day is reconstructable from cold storage." if ok else
          "FAIL - ingest this date first: fb ingest --start " + args.on)
    return 0 if ok else 1


# ------------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="fb", description="Financial-Brain")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("migrate", help="create/update the schema").set_defaults(fn=cmd_migrate)
    sub.add_parser("status", help="what the system currently holds").set_defaults(fn=cmd_status)

    g = sub.add_parser("ingest", help="ingest bhavcopies for a date range")
    g.add_argument("--start", required=True)
    g.add_argument("--end")
    g.add_argument("--source", action="append", choices=["NSE", "BSE"])
    g.add_argument("--force", action="store_true", help="re-ingest dates already done")
    g.add_argument("--from-lake", action="store_true", help="replay from stored bytes, no network")
    g.add_argument("--strict", action="store_true", help="non-zero exit if any run failed")
    g.add_argument("-v", "--verbose", action="store_true")
    g.set_defaults(fn=cmd_ingest)

    g = sub.add_parser("runs", help="ingestion history")
    g.add_argument("--failed", action="store_true")
    g.add_argument("--limit", type=int, default=20)
    g.set_defaults(fn=cmd_runs)

    g = sub.add_parser("dq", help="failing data-quality checks")
    g.add_argument("--limit", type=int, default=30)
    g.set_defaults(fn=cmd_dq)

    g = sub.add_parser("resolve", help="resolve a ticker/ISIN/code to ISIN identity")
    g.add_argument("identifier")
    g.add_argument("--on", help="resolve as of this date (YYYY-MM-DD)")
    g.set_defaults(fn=cmd_resolve)

    g = sub.add_parser("universe", help="point-in-time universe")
    g.add_argument("--on", required=True)
    g.add_argument("--exchange")
    g.add_argument("--min-turnover", type=float)
    g.add_argument("--churn-from", help="show churn between this date and --on")
    g.add_argument("--limit", type=int, default=25)
    g.set_defaults(fn=cmd_universe)

    g = sub.add_parser("pit", help="point-in-time observation store")
    g.add_argument("--backfill-closes", metavar="DATE")
    g.add_argument("--exchange")
    g.add_argument("--history", metavar="ISIN:ATTRIBUTE")
    g.set_defaults(fn=cmd_pit)

    g = sub.add_parser("costs", help="Indian transaction cost model")
    g.add_argument("--turnover", type=float, default=100_000)
    g.add_argument("--segment", default="delivery",
                   choices=[s.value for s in Segment])
    g.add_argument("--bucket", default="large",
                   choices=["mega", "large", "mid", "small", "micro"])
    g.set_defaults(fn=cmd_costs)

    g = sub.add_parser("actions", help="corporate actions and adjustments")
    g.add_argument("--rebuild", action="store_true")
    g.add_argument("--suspicious", action="store_true")
    g.add_argument("--threshold", type=float, default=0.20)
    g.add_argument("--limit", type=int, default=25)
    g.set_defaults(fn=cmd_actions)

    g = sub.add_parser("check", help="Phase 0 exit test for a date")
    g.add_argument("--on", required=True)
    g.set_defaults(fn=cmd_check)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
