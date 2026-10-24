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
from .corpactions import detect as ca_detect
from .costs.india import DEFAULT as COSTS, Segment
from .ingest.job import BhavcopyIngestJob
from .ingest.prefetch import prefetch
from .ingest.reference import EquityListJob, FnoEligibilityJob, IndexCloseJob
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


def cmd_inverse(args) -> int:
    """Research-only action-policy reconstruction; never places an order."""
    from .research.inverse_strategy import main as inverse_main
    argv = ["--seed", str(args.seed), "--rows", str(args.rows)]
    if args.synthetic:
        argv += ["--synthetic", args.synthetic]
    if args.history:
        argv += ["--history", args.history]
    if args.authorized:
        argv.append("--authorized")
    if args.rules_out:
        argv += ["--rules-out", args.rules_out]
    if args.report_out:
        argv += ["--report-out", args.report_out]
    return inverse_main(argv)


def cmd_inverse_audit(args) -> int:
    """Report local research-history coverage without reading credentials or placing orders."""
    from .research.history_audit import audit_existing_history
    with Database(load(), read_only=True).connect() as con:
        print(json.dumps(audit_existing_history(con), indent=2))
    return 0


def cmd_inverse_tool(args) -> int:
    from .research.workstation import main as workstation_main
    return workstation_main(args.args)


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
        ("adjustment factors", "SELECT COUNT(*) FROM adjustment_factors"),
        ("index levels", "SELECT COUNT(*) FROM index_levels"),
        ("reference rows", "SELECT COUNT(*) FROM security_reference"),
        ("F&O eligible", "SELECT COUNT(*) FROM security_flags WHERE flag='FNO_ELIGIBLE'"),
        ("rejected rows", "SELECT COUNT(*) FROM rejected_rows"),
    ]:
        print(f"{label:<20}{db.query(sql)[0][0]:,}")

    rng = db.query("SELECT MIN(business_date), MAX(business_date) FROM universe_snapshots")[0]
    if rng[0]:
        days = db.query("SELECT COUNT(DISTINCT business_date) FROM universe_snapshots")[0][0]
        print(f"{'coverage':<20}{rng[0]} .. {rng[1]}  ({days} trading days)")

    # A past failure that a later run fixed is history, not an outstanding problem.
    unresolved = db.query("""
        SELECT COUNT(*) FROM (
            SELECT source, dataset, business_date FROM ingest_runs
            WHERE status NOT IN ('ok','ok_partial','skipped','not_published')
            EXCEPT
            SELECT source, dataset, business_date FROM ingest_runs
            WHERE status IN ('ok','ok_partial','not_published'))""")[0][0]
    print(f"{'unresolved dates':<20}{unresolved}")
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
            if ":" not in args.history:
                print("expected ISIN:ATTRIBUTE, e.g. INE467B01029:revenue")
                return 1
            entity, attr = args.history.split(":", 1)
            rows = store.history(entity.strip(), attr.strip())
            if not rows:
                print(f"no observations for {entity}:{attr}")
            else:
                print(f"restatement trail for {entity}:{attr} (oldest first)")
                _print_table(rows)
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


def cmd_reference(args) -> int:
    """Sync exchange reference data: master list, F&O eligibility."""
    Database(load()).migrate()
    print(EquityListJob().run())
    print(FnoEligibilityJob().run())
    return 0


def cmd_index(args) -> int:
    """Ingest NSE index closes - the benchmark history."""
    Database(load()).migrate()
    start = _d(args.start)
    end = _d(args.end) if args.end else start
    counts: dict[str, int] = {}
    for res in IndexCloseJob().run_range(start, end, force=args.force):
        counts[res.status] = counts.get(res.status, 0) + 1
        if not res.ok or args.verbose:
            print(res)
    print("\nsummary: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 0


def cmd_derive(args) -> int:
    """Derive corporate actions from exchange-restated previous closes."""
    db = Database(load())
    with db.connect() as con:
        if args.dry_run:
            graded = ca_detect.corroborate(
                ca_detect.find_candidates(con, min_deviation=args.min_deviation))
            corr = [g for g in graded if g["confidence"] == "corroborated"]
            print(f"{len(graded)} candidates, {len(corr)} corroborated across both exchanges\n")
            _print_table([
                {"ex_date": g["ex_date"], "ticker": g["ticker"], "isin": g["isin"],
                 "factor": round(g["factor"], 4), "type": g["action_type"],
                 "ratio": (f'{int(g["ratio_from"])}:{int(g["ratio_to"])}'
                           if g["ratio_from"] else "-"),
                 "confidence": g["confidence"], "exchanges": g["exchanges"]}
                for g in graded], limit=args.limit)
            return 0

        if args.rebuild:
            print(f"cleared {ca_detect.clear_derived(con)} derived action(s) for rebuild")
        out = ca_detect.derive_all(
            con, min_deviation=args.min_deviation,
            corroborated_only=not args.include_single_exchange)
        a, b = out["restated_prev"], out["close_gap"]
        print("A. exchange-restated previous close")
        print(f"   candidates    {a['candidates']}  "
              f"(corroborated {a['corroborated']}, single-exchange {a['single_exchange']})")
        print(f"   recorded      {a['written']}")
        print("B. corroborated close-to-close gaps")
        print(f"   candidates    {b['candidates']}")
        print(f"   recorded      {b['written']}")
        print(f"   skipped       {b['skipped_no_clean_ratio']} no clean ratio "
              f"(likely genuine price moves), "
              f"{b['skipped_single_exchange']} single-exchange")
        c = out["succession"]
        print("C. ISIN successions (split changes the ISIN)")
        print(f"   successions   {c['successions']}  (corroborated {c['corroborated']})")
        print(f"   recorded      {c['written']}")
        print(f"   skipped       {c['identity_only']} identity-only, "
              f"{c['no_clean_ratio']} no clean ratio, "
              f"{c['already_recorded']} already recorded")
        n = con.execute("SELECT COUNT(*) FROM adjustment_factors").fetchone()[0]
        print(f"adjustment factors rebuilt: {n}")
    return 0


def cmd_benchmark(args) -> int:
    db = Database(load())
    rows = db.query_dicts("""
        SELECT index_name, MIN(business_date) AS first, MAX(business_date) AS last,
               COUNT(*) AS days
        FROM index_levels GROUP BY index_name ORDER BY days DESC, index_name LIMIT ?""",
        [args.limit])
    if not rows:
        print("no index history yet - run `fb index --start <date> --end <date>`")
        return 0
    print(f"{len(rows)} indices shown")
    _print_table(rows, limit=args.limit)
    return 0


def cmd_gate(args) -> int:
    """Phase 0 completion gate.

    Phase 1 builds perception on top of this data. If any base here is unsound, every
    brief, thesis and backtest built later inherits the fault - so this refuses to bless
    Phase 1 rather than letting it start quietly on a broken foundation.
    """
    db = Database(load())
    db.migrate()
    checks: list[tuple[str, bool, str, str]] = []

    def add(name, passed, observed, why):
        checks.append((name, bool(passed), str(observed), why))

    with db.connect() as con:
        q = lambda sql, p=None: con.execute(sql, p or []).fetchone()[0]

        days = q("SELECT COUNT(DISTINCT business_date) FROM universe_snapshots")
        add("price history", days >= args.min_days, f"{days} trading days",
            f"need >= {args.min_days} to see regime variety and any corporate actions")

        both = q("""SELECT COUNT(*) FROM (SELECT business_date FROM universe_snapshots
                    GROUP BY business_date HAVING COUNT(DISTINCT exchange) = 2)""")
        add("both exchanges", both >= days * 0.9, f"{both}/{days} days have NSE and BSE",
            "cross-exchange corroboration needs both present")

        isins = q("SELECT COUNT(*) FROM securities")
        add("security master", isins > 1000, f"{isins} ISINs",
            "ISIN-keyed identity is the join key for everything downstream")

        ref = q("SELECT COUNT(*) FROM security_reference WHERE listing_date IS NOT NULL")
        add("listing dates", ref > 1000, f"{ref} securities with listing dates",
            "bounds how far back a name could belong to any universe")

        fno = q("SELECT COUNT(*) FROM security_flags WHERE flag = 'FNO_ELIGIBLE'")
        add("short-ability flag", fno > 100, f"{fno} F&O-eligible names",
            "the binary can-this-be-shorted test the implementability gate needs")

        idx = q("SELECT COUNT(DISTINCT business_date) FROM index_levels")
        add("benchmark history", idx >= days * 0.9, f"{idx}/{days} days of index levels",
            "no strategy can be assessed without something to assess it against")

        acts = q("SELECT COUNT(*) FROM corporate_actions")
        factors = q("SELECT COUNT(*) FROM adjustment_factors")
        add("corporate actions", acts > 0, f"{acts} actions, {factors} adjustment factors",
            "unadjusted splits make price history silently wrong")

        untriaged = len(_unexplained_gaps(con))
        reviewed = q("SELECT COUNT(*) FROM gap_reviews")
        add("gaps all triaged", untriaged == 0,
            f"{untriaged} untriaged, {reviewed} reviewed",
            "a 40% overnight move is either a corporate action or a real move - "
            "either way it must be examined once (see `fb gaps`)")

        lake = q("SELECT COUNT(*) FROM lake_manifest")
        add("raw lake", lake > 0, f"{lake} immutable payloads",
            "every curated row must be rebuildable from stored bytes")

        orphans = q("""SELECT COUNT(*) FROM ingest_runs r WHERE r.status = 'ok'
                       AND r.lake_key IS NOT NULL
                       AND NOT EXISTS (SELECT 1 FROM lake_manifest m WHERE m.key = r.lake_key)""")
        add("lineage intact", orphans == 0, f"{orphans} runs cite a missing lake object",
            "provenance must resolve or the audit trail is broken")

        # A past failure that a later run fixed is history, not an outstanding problem.
        # The question is whether any (source, dataset, date) is *currently* unresolved.
        unresolved = q("""
            SELECT COUNT(*) FROM (
                SELECT source, dataset, business_date FROM ingest_runs
                WHERE status NOT IN ('ok','ok_partial','skipped','not_published')
                EXCEPT
                SELECT source, dataset, business_date FROM ingest_runs
                WHERE status IN ('ok','ok_partial','not_published'))""")
        add("no unresolved dates", unresolved == 0,
            f"{unresolved} (source, date) pairs never succeeded",
            "an unresolved date is a hole in the history")

        # A check that cannot fail is worse than no check. Phase 0 cannot *populate*
        # the PIT store - fundamentals arrive with filings ingestion in Phase 2 - so what
        # it can guarantee is that the store is structurally correct and ready to record.
        # PRAGMA table_info returns (cid, name, type, notnull, dflt_value, pk).
        cols = {r[1] for r in con.execute("PRAGMA table_info('pit_observations')").fetchall()}
        required = {"observation_id", "entity_key", "attribute", "published_at",
                    "observed_at", "source_tier", "revision_of", "evidence_key"}
        missing = required - cols
        n_obs = q("SELECT COUNT(*) FROM pit_observations")
        no_ts = q("SELECT COUNT(*) FROM pit_observations WHERE observed_at IS NULL")
        add("PIT store correct", not missing and no_ts == 0,
            (f"{n_obs} observations, schema complete"
             if not missing else f"missing columns: {sorted(missing)}"),
            "published_at/observed_at/revision_of are what make as_of() honest; "
            "population begins with filings in Phase 2")

    width = max(len(c[0]) for c in checks)
    print("PHASE 0 COMPLETION GATE")
    print("=" * 74)
    for name, passed, observed, why in checks:
        print(f"[{'PASS' if passed else 'FAIL'}] {name.ljust(width)}  {observed}")
        if not passed:
            print(f"       {' ' * width}  why it matters: {why}")
    failures = [c for c in checks if not c[1]]
    print("=" * 74)
    if failures:
        print(f"{len(failures)} base(s) incomplete - Phase 1 should not start yet.")
        print("Phase 1 builds perception on this data; every fault here is inherited.")
        return 1
    print(f"All {len(checks)} bases complete. Phase 1 may begin.")
    return 0


def _unexplained_gaps(con, threshold: float = 0.35, min_turnover: float = 10_000_000.0):
    """Large moves with no recorded action or review - one shared definition."""
    return ca_detect.find_untriaged_gaps(con, threshold=threshold,
                                         min_turnover=min_turnover)


def cmd_gaps(args) -> int:
    """List or triage large overnight gaps with no recorded corporate action.

    A stock genuinely can move 40% overnight, so the standard is not "no gaps" but
    "no gap left unexamined".
    """
    from .corpactions.detect import PLAUSIBLE_RATIOS
    db = Database(load())
    with db.connect() as con:
        if args.review:
            try:
                isin, ex_date, verdict = args.review.split(":", 2)
            except ValueError:
                print("expected ISIN:YYYY-MM-DD:verdict[:note]")
                return 1
            note = ""
            if ":" in verdict:
                verdict, note = verdict.split(":", 1)
            if verdict not in ("price_move", "action_recorded", "needs_source"):
                print("verdict must be price_move | action_recorded | needs_source")
                return 1
            con.execute(
                """INSERT INTO gap_reviews
                   (isin, ex_date, verdict, note, reviewed_at)
                   VALUES (?,?,?,?,now())
                   ON CONFLICT (isin, ex_date) DO UPDATE SET
                       verdict = EXCLUDED.verdict, note = EXCLUDED.note,
                       reviewed_at = EXCLUDED.reviewed_at""",
                [isin.strip(), _d(ex_date.strip()), verdict, note.strip()])
            print(f"reviewed {isin} {ex_date}: {verdict}")
            return 0

        if args.auto_review:
            out = ca_detect.auto_triage_gaps(con, threshold=args.threshold,
                                             redo=args.redo)
            print(f"auto-triaged {out['events']} gap event(s)")
            print(f"  action_recorded {out['action_recorded']}  "
                  f"(explained by a BSE-reported or derived action)")
            print(f"  price_move      {out['price_move']}  "
                  f"(shock day, intraday move, or one-sided on a cross-listed ISIN)")
            print(f"  needs_source    {out['needs_source']}  "
                  f"(undecidable without a corporate-action feed)")
            return 0

        rows = _unexplained_gaps(con, threshold=args.threshold)
        if not rows:
            print("no untriaged gaps")
            return 0
        print(f"{len(rows)} untriaged gap(s) >= {args.threshold:.0%}\n")
        out = []
        for r in rows:
            f = r["factor"]
            best = min(PLAUSIBLE_RATIOS, key=lambda ab: abs(ab[0] / ab[1] - f))
            err = abs(best[0] / best[1] - f) / f
            out.append({"ex_date": r["ex_date"], "exch": r["exchange"],
                        "ticker": r["ticker"], "isin": r["isin"],
                        "factor": round(f, 4),
                        "nearest_ratio": f"{best[0]}:{best[1]}",
                        "snap_error": f"{err:.1%}"})
        _print_table(out, limit=args.limit)
        print("\n  triage with:  fb gaps --review ISIN:YYYY-MM-DD:verdict:note")
        print("  verdicts: price_move | action_recorded | needs_source")
    return 0


def cmd_prefetch(args) -> int:
    """Fill the raw lake from the network in parallel. Touches no database."""
    start, end = _d(args.start), _d(args.end) if args.end else (_d(args.start),) * 2
    if args.end:
        end = _d(args.end)
    else:
        end = start

    def progress(done, total, stats):
        pct = done / total * 100
        print(f"  {done}/{total} ({pct:.0f}%)  {stats}", flush=True)

    stats = prefetch(start, end, sources=args.source, kind=args.kind,
                     workers=args.workers, progress=progress)
    print(f"{chr(10)}prefetch complete: {stats}")
    if stats.failures:
        print(f"{chr(10)}{len(stats.failures)} failure(s), first few:")
        for f in stats.failures[:10]:
            print("  ", f)
    print(f"{chr(10)}now load it with:  fb ingest --start "
          f"{args.start} --end {args.end or args.start} --from-lake")
    return 0


def cmd_lake_register(args) -> int:
    """Register every lake object in lake_manifest.

    `fb prefetch` lands bytes without touching the database, so objects it fetched have
    no provenance row until they are loaded. This closes that gap in one pass; it is
    idempotent and reads only sidecar metadata, never the payloads.
    """
    from .lake.store import RawLake
    cfg = load()
    lake = RawLake(cfg.lake)
    db = Database(cfg)
    rows = [[o.key, o.source, o.dataset, o.business_date, o.filename, o.url,
             o.retrieved_at, o.sha256, o.size_bytes, o.http_status, o.content_type]
            for o in lake.iter_objects()]
    with db.connect() as con:
        before = con.execute("SELECT COUNT(*) FROM lake_manifest").fetchone()[0]
        con.executemany(
            """INSERT INTO lake_manifest
               (key, source, dataset, business_date, filename, url, retrieved_at,
                sha256, size_bytes, http_status, content_type)
               VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (key) DO NOTHING""", rows)
        after = con.execute("SELECT COUNT(*) FROM lake_manifest").fetchone()[0]
    print(f"{len(rows)} lake objects; manifest {before} -> {after} (+{after - before})")
    return 0


def cmd_kite_index_fill(args) -> int:
    """Fill index-close sessions NSE does not hold, from Kite (validated, fill-only)."""
    from .ingest import kite_fill
    from .lake.store import RawLake
    from .providers.kite import KiteIndexHistoryProvider, KiteNotConfigured, credentials
    try:
        credentials()
    except KiteNotConfigured as e:
        print(e)
        return 2
    cfg = load()
    with Database(cfg).connect() as con:
        out = kite_fill.fill(con, KiteIndexHistoryProvider(), RawLake(cfg.lake),
                             indices=args.index or None)
    print(f"{out['missing_dates']} session(s) without index levels")
    for name, msg in sorted(out["indices"].items()):
        print(f"  {name:<28} {msg}")
    return 0


def cmd_corpact_feed(args) -> int:
    """Ingest BSE's corporate-action feed (Tier 1), then rebuild factors and reconcile."""
    from .ingest import corpact_feed
    cfg = load()
    start = datetime.strptime(args.start, "%Y-%m").date()
    end = datetime.strptime(args.end, "%Y-%m").date() if args.end else date.today()
    with Database(cfg).connect() as con:
        st = corpact_feed.ingest(con, cfg, start, end)
        print(f"{st['months']} month(s), {st['fetched']} fetched, {st['rows']} feed rows")
        print(f"  recorded   {st['recorded']} reported action(s)")
        print(f"  unmapped   {st['unmapped']} (scrip code not listed on BSE near the ex-date)")
        print(f"  no ex-date {st['no_ex_date']}")
        n = ca_detect.rebuild_derived_factors(con)
        print(f"adjustment factors rebuilt: {n}")
        _print_reconcile(corpact_feed.reconcile(con))
    return 0


def cmd_corpact_reconcile(args) -> int:
    from .ingest import corpact_feed
    with Database(load()).connect() as con:
        _print_reconcile(corpact_feed.reconcile(con))
    return 0


def _print_reconcile(r: dict) -> None:
    pct = lambda x: "n/a" if x is None else f"{x:.1%}"
    print("derived vs reported (BSE feed)")
    print(f"  precision  {pct(r['precision'])}  - {r['derived_matched']} of {r['derived']} "
          f"derived actions match a reported one")
    print(f"             {r['derived_wrong_ratio']} matched in time but wrong ratio, "
          f"{r['derived_unreported']} with nothing reported")
    print(f"  recall     {pct(r['recall'])}  - {r['reported_found']} of "
          f"{r['reported_liquid']} reported splits/bonuses on liquid names were derived")


def cmd_announcements(args) -> int:
    """Ingest BSE announcements (lake-first) and classify them."""
    from .ingest.announcements import AnnouncementsJob
    Database(load()).migrate()
    start, end = _d(args.start), _d(args.end) if args.end else _d(args.start)
    counts: dict[str, int] = {}
    for res in AnnouncementsJob().run_range(start, end, force=args.force):
        counts[res.status] = counts.get(res.status, 0) + 1
        if args.verbose or res.status not in ("ok", "skipped", "not_published"):
            print(res)
    print("summary: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 0


def cmd_features(args) -> int:
    """Build adjusted continuous prices and the versioned feature table (P2-1)."""
    from .features import indicators
    with Database(load()).connect() as con:
        print(indicators.build(con))
        for r in con.execute("""SELECT business_date, COUNT(*), COUNT(ret_250d), COUNT(vol_60)
                                FROM features GROUP BY 1 ORDER BY 1 DESC LIMIT 3""").fetchall():
            print(f"{r[0]}  lineages {r[1]}  with ret_250d {r[2]}  with vol_60 {r[3]}")
    return 0


def cmd_evaluate(args) -> int:
    """Rank-IC benchmark of each feature at a horizon (P2-3). Facts about the past."""
    from .evaluation import benchmark, labels
    if args.classifier:
        for name in labels.SETS:
            r = labels.score(name)
            print(f"classifier on {name:<8} {r['correct']}/{r['n']} = {r['precision']:.1%}")
            for m in r["misses"]:
                print(f"   miss {m['predicted']} -> {m['gold']}  {m['note']}")
        return 0
    feats = args.feature or sorted(benchmark.FEATURES)
    if args.validate:
        from .evaluation import firewall
        with Database(load()).connect() as con:
            for f in feats:
                r = firewall.validate(con, f, args.horizon, start=args.start, end=args.end)
                print(f"{f:<14} {r['verdict']:<8} trial {r['trials']}  IC {r['mean_ic']:+.3f} "
                      f"t {r['ic_t']:+.1f}  DSR {r['deflated_sharpe']:.2f}  "
                      f"net/period {r['net_per_period']:+.2%}  turnover {r['turnover']:.0%}")
                for why in r["reasons"]:
                    print(f"    - {why}")
        return 0
    with Database(load()).connect() as con:
        for f in feats:
            r = benchmark.evaluate(con, f, args.horizon, start=args.start, end=args.end)
            print(f"{f:<14} h={r['horizon']:<3} dates {r['dates']:<4} IC {r['mean_ic']:+.3f} "
                  f"t {r['ic_t']:+.1f} hit {r['hit_rate']:.0%} Q5-Q1 {r['mean_spread']:+.2%} "
                  f"names {r['avg_names']:.0f} filled {r['filled_no_outcome']}")
    return 0


def cmd_graph(args) -> int:
    """Promoter-group knowledge graph (P2-4): build it, or profile one company."""
    from .graph import build
    as_of = _d(args.as_of) if args.as_of else None
    with Database(load()).connect() as con:
        if args.build:
            print(build.build(con))
        if args.isin:
            p = build.profile(con, args.isin, as_of)
            g = p["group"]
            print(f"{args.isin}  pledge filings last {p['recent_days']}d: "
                  f"{p['pledge_filings_recent']} by {p['pledge_filers_recent']} filer(s)")
            if g:
                print(f"group ({len(g['members'])}, anchor {g['anchor']}): "
                      + ", ".join(g["companies"]))
            for f in p["filers"][:args.limit]:
                print(f"  {f['relation']:<11} {f['filings']:>4}  last {f['last']}  "
                      f"{f['filer']} [{f['kind']}]  e.g. {f['news_ids'][0]}")
        else:
            for g in build.groups(con, as_of)[:args.limit]:
                print(f"{len(g['members']):>3}  {g['anchor'] or '-':<40} "
                      + ", ".join(g["companies"][:6]) + (" ..." if len(g["members"]) > 6 else ""))
    return 0


def cmd_hypothesis(args) -> int:
    """Pre-registered hypotheses: register (from TOML), test once in-sample, then OOS."""
    import tomllib
    from .evaluation import registry
    with Database(load()).connect() as con:
        if args.action == "register":
            spec = tomllib.loads(open(args.target, encoding="utf-8").read())
            print(registry.register(con, spec))
        elif args.action == "test":
            r = registry.test(con, args.target,
                              mode="out_of_sample" if args.oos else "in_sample")
            print(f"{r['hypothesis_id']} {r['mode']}: {r['verdict']}")
            for why in r.get("reasons", []):
                print(f"    - {why}")
        else:
            for row in con.execute("""SELECT h.hypothesis_id, h.name, h.data_cutoff,
                    STRING_AGG(t.mode || '=' || t.verdict, ', ' ORDER BY t.tested_at)
                    FROM hypotheses h LEFT JOIN hypothesis_tests t USING (hypothesis_id)
                    GROUP BY ALL ORDER BY MIN(h.registered_at)""").fetchall():
                print(f"{row[0]}  {row[1]:<40} cutoff {row[2]}  {row[3] or 'untested'}")
    return 0


def cmd_paper(args) -> int:
    """Paper trading for decisions at PAPER_CANDIDATE: open, mark (close due), list."""
    from .paper import ledger as paper
    with Database(load()).connect() as con:
        if args.action == "open":
            print(paper.open_trade(con, args.decision_id))
        elif args.action == "mark":
            print(paper.mark(con))
        for r in con.execute("""SELECT decision_id, action, isin, entry_date, due_date, status,
                                ROUND(excess * 100, 2) FROM paper_trades
                                ORDER BY entry_date DESC LIMIT 20""").fetchall():
            print(f"{r[0]}  {r[1]:<6} {r[2]}  {r[3]} -> {r[4]}  {r[5]:<6} "
                  f"{'' if r[6] is None else f'excess {r[6]:+.2f}%'}")
        print(paper.scoreboard(con))
    return 0


def cmd_models(args) -> int:
    """Tiered models (ADR-0002): registry, benchmark on labelled tasks, router plan."""
    from .evaluation import models as bench
    from .llm import backends, router
    with Database(load()).connect() as con:
        if args.action == "bench":
            for model in args.model:
                r = bench.bench(con, args.task, model, target=args.target, limit=args.limit)
                print(f"{model:<20} acc {r['accuracy']:.3f} F1 {r['macro_f1']:.3f} "
                      f"threshold {r['threshold']} coverage {r['coverage']:.2f} "
                      f"{r['latency_ms']:.0f} ms  {r['top_confusions'][:3]}")
        elif args.action == "rescore":
            for r in bench.rescore(con, args.task, target=args.target):
                t = {k: v for k, v in r["thresholds"].items() if v is not None}
                print(f"{r['model']:<20} F1 {r['macro_f1']:.3f} coverage {r['coverage']:.2f}"
                      f"  per-label thresholds {t}")
        elif args.action == "route":
            best = router.choose(con, args.task, budget_ms=args.budget_ms,
                                 verify_on=args.verify_on,
                                 max_accepted_error=args.max_accepted_error)
            if not best.get("chain"):
                print(best.get("note"))
                for r in best.get("rejected", []):
                    print(f"   rejected {r}")
            else:
                print(" -> ".join(s["model"] for s in best["chain"]))
                for name in ("simulated", "verified"):
                    sim = best.get(name)
                    if sim:
                        print(f"  {name:<9} ({sim['set']}): accuracy {sim['accuracy']:.3f}, "
                              f"uncertain {sim['uncertain']:.0%}, wrong when accepted "
                              f"{sim['wrong_when_accepted']:.0%}, {sim['latency_ms']:.0f} ms")
        elif args.action == "plan":
            steps = router.plan(con, args.task)
            for s in steps:
                per = {k: v for k, v in (s.get("thresholds") or {}).items() if v is not None}
                bars = ", ".join(f"{k} >= {v:.3f}" for k, v in per.items()) or                     f">= {s['threshold']:.3f}"
                print(f"tier {s['tier']}  {s['model']:<20} F1 {s['macro_f1']:.3f}  "
                      f"{s['latency_ms']:.0f} ms  accepts: {bars}")
            try:
                r = bench.simulate_route(con, args.task, steps)
                print(f"route end-to-end: accuracy {r['accuracy']:.3f}, uncertain "
                      f"{r['uncertain']:.0%}, {r['latency_ms']:.0f} ms per item, answered by "
                      + ", ".join(f"{m} {v:.0%}" for m, v in r["answered_by"].items()))
            except ValueError as e:
                print(f"(no replay: {e})")
        else:
            local = backends.ollama_models()
            latest = {(t, m): (f1, thr, lat) for t, m, f1, thr, lat in con.execute("""
                SELECT task, model, macro_f1, threshold, latency_ms FROM (SELECT *,
                ROW_NUMBER() OVER (PARTITION BY task, model ORDER BY run_at DESC) rk
                FROM model_bench) WHERE rk = 1""").fetchall()}
            names = ["rules", "finbert"] + list(router.registry())
            for n in names:
                here = "local" if n in local or n in router.BUILTIN_TIER else (
                    "cloud" if router.tier(n) >= 3 else "not pulled")
                res = "; ".join(f"{t}: F1 {v[0]:.2f} thr {v[1]} {v[2]:.0f}ms"
                                for (t, m), v in latest.items() if m == n)
                print(f"tier {router.tier(n)}  {n:<22} {here:<10} {res}")
    return 0


def cmd_calls(args) -> int:
    """Read earnings-call transcripts and investor presentations for a day."""
    from .docintel import calls as parse
    from .ingest import calls as ing
    cfg = load()
    with Database(cfg).connect() as con:
        print(ing.read_day(con, cfg, _d(args.date), limit=args.limit,
                           embed=not args.no_embed))
        for nid, company, pages, qa in con.execute("""
                SELECT news_id, company, pages, has_qa FROM call_documents
                WHERE called_on = ? ORDER BY fetched_at DESC LIMIT 8""",
                                                   [_d(args.date)]).fetchall():
            print(f"  {company[:30]:<30} {pages:>3} pages  "
                  f"{'Q&A' if qa else 'no Q&A':<7}")
            for s in parse.most_similar(con, nid, k=2):
                print(f"      resembles {s['company'][:26]:<26} {s['called_on']} "
                      f"({s['similarity']:.2f})")
    return 0


def cmd_vault(args) -> int:
    """Export the Obsidian vault as a link graph, and report broken links."""
    from pathlib import Path

    from . import vault
    cfg = load()
    out = vault.write(Path(args.root), Path(args.out or cfg.data_root / "brain_graph.json"))
    print(f"{out['notes']} notes, {out['links']} links -> {out['out']}")
    if out["broken_links"]:
        print(f"broken links ({len(out['broken_links'])}): "
              + ", ".join(out["broken_links"][:10]))
    else:
        print("no broken links")
    return 0


def cmd_timing(args) -> int:
    """Fit, calibrate and verify the System One timing model - or report its refusal."""
    from .evaluation import timing
    with Database(load()).connect() as con:
        if args.action == "fit":
            model = timing.build(con)
            timing.save(con, model)
            r = model.report
            print(f"rows {r.get('rows')}  base rate {r.get('base_rate_test')}")
            print(f"threshold {r.get('threshold_from_calibration')}  "
                  f"coverage {r.get('coverage_test')}  "
                  f"precision {r.get('precision_test')}  "
                  f"lift {r.get('lift_over_base')}")
            print(f"verdict: {r.get('verdict')}")
        else:
            model = timing.latest(con)
            if not model:
                print("no timing model fitted yet")
                return 0
            print(f"usable: {model.usable()}  threshold {model.threshold}")
            print(f"verdict: {model.report.get('verdict')}")
            if args.isin:
                d = timing.decide(con, args.isin, _d(args.date) if args.date
                                  else date.today(), model=model)
                print(f"{args.isin}: {d.label} (confidence {d.confidence:.3f}) "
                      f"{d.extra}")
    return 0


def cmd_quality(args) -> int:
    """Score decisions on process, apart from what they earned."""
    from .decisions import quality
    with Database(load()).connect() as con:
        if args.decision:
            s = quality.assess_decision(con, args.decision)
            print(f"{args.decision}: {s.describe()}")
            for part, value in sorted(s.parts.items(), key=lambda kv: kv[1]):
                print(f"    {part:<16} {value:.0%}")
            for note in s.notes:
                print(f"    note: {note}")
            return 0
        out = quality.against_outcomes(con)
        if not out.get("n"):
            print("no closed trades to score yet")
            return 0
        print(f"{out['n']} closed decisions, mean process quality "
              f"{out['mean_quality']:.0%}")
        for label in ("well_made", "poorly_made"):
            part = out[label]
            got = (f"{part['mean_excess']:+.2%}" if part["mean_excess"] is not None
                   else "-")
            print(f"  {label.replace('_', ' '):<12} {part['n']:>3} trades, "
                  f"mean excess {got}")
        if "quality_premium" in out:
            premium = out["quality_premium"]
            print(f"  quality premium {premium:+.2%} - "
                  + ("better process paid" if premium > 0 else
                     "better process did not pay, which says the edge is missing "
                     "rather than the process"))
    return 0


def cmd_analogues(args) -> int:
    """What followed this kind of event before - the distribution, not an opinion."""
    from .features import analogues
    with Database(load()).connect() as con:
        as_of = _d(args.date) if args.date else date.today()
        if args.warm:
            built = analogues.warm(con, as_of, horizon_days=args.horizon)
            usable = sum(1 for b in built if b["usable"])
            print(f"cached {len(built)} event types as of {as_of}; "
                  f"{usable} have enough history to size a trade on")
            return 0
        if not args.event_type:
            rows = con.execute("""SELECT event_type, cases, mean_excess FROM
                                  analogue_distributions WHERE as_of_month <= ?
                                  AND horizon_days = ? ORDER BY mean_excess DESC NULLS LAST""",
                               [as_of.replace(day=1), args.horizon]).fetchall()
            if not rows:
                print("nothing cached yet - run: fb analogues --warm")
                return 0
            print(f"{'event type':<26} {'cases':>7}  mean excess over {args.horizon}d")
            for name, cases, mean in rows:
                mark = " " if cases >= analogues.MIN_CASES else "*"
                print(f"{name:<26} {cases:>7}{mark} "
                      + (f"{mean:+.2%}" if mean is not None else "-"))
            print(f"\n* fewer than {analogues.MIN_CASES} cases: no scenarios are "
                  "produced, so a decision resting on one becomes a NO TRADE")
            return 0

        s = analogues.summary(con, args.event_type, as_of, horizon_days=args.horizon)
        if not s["cases"]:
            print(f"no resolved history for {args.event_type} as of {as_of}")
            return 0
        print(f"{args.event_type} as of {as_of}: {s['cases']} resolved cases over "
              f"{args.horizon} days")
        print(f"  mean excess   {s['mean_excess']:+.2%}   "
              f"median {s['median_excess']:+.2%}   win rate {s['win_rate']:.0%}")
        print(f"  worst {s['worst']:+.1%}   best {s['best']:+.1%}")
        if not s["scenarios"]:
            print(f"  too thin: under {analogues.MIN_CASES} cases no distribution is "
                  "offered, and a thesis resting on this cannot be sized")
            return 0
        from .decisions import expected_value as ev
        for name, band in s["scenarios"].items():
            print(f"  {name:<6} p={band['probability']:.0%}  "
                  f"{band['return']:+.2%}  ({band['cases']} cases)")
        a = ev.assess(s["scenarios"])
        print(f"  {a.describe()}")
        rr = a.reward_to_risk
        print(f"  reward to risk {rr:.2f}" if rr else "  no losing band")
    return 0


def cmd_reclassify(args) -> int:
    """Re-apply the current event rules to the whole archive.

    Classification happens once, at ingest, so a corrected rule reaches only future
    filings until this is run - leaving the record split between filings typed under the
    old rule and filings typed under the new one.
    """
    from .events.classify import reclassify
    with Database(load()).connect() as con:
        out = reclassify(con, dry_run=not args.apply)
        verb = "changed" if out["applied"] else "would change"
        print(f"scanned {out['scanned']:,}  {verb} {out['changed']:,}")
        for transition, n in out["transitions"].items():
            print(f"  {n:>8}  {transition}")
        if out["changed"] and not out["applied"]:
            print("\nnothing written; pass --apply to sweep the archive")
        elif out["applied"]:
            print("\nrebuild what derives from event types: fb features build")
    return 0


def cmd_strategy(args) -> int:
    """Stateful entry/exit strategies, through the same gates as any factor."""
    from .evaluation import timeseries as ts
    if args.list:
        for name, spec in ts.STRATEGIES.items():
            print(f"{name}\n    {spec['claim']}\n    source: {spec['source']}")
        return 0
    names = args.strategy or list(ts.STRATEGIES)
    with Database(load()).connect() as con:
        for name in names:
            if name not in ts.STRATEGIES:
                print(f"{name}: unknown strategy; --list shows them")
                continue
            r = ts.validate(con, name, start=args.start, end=args.end,
                            record=not args.dry_run)
            print(f"{name:<26} {r['verdict']:<7} trial {r['trials']:>3}  "
                  f"t {r['excess_t']:+6.2f}  DSR {r['deflated_sharpe']:.2f}  "
                  f"net/session {r['mean_excess']:+.4%}  turn {r['turnover']:.2%}  "
                  f"held {r['avg_held']:.0f}  invested {r['invested_days']:.0%}")
            for why in r["reasons"]:
                print(f"      - {why}")
    return 0


def cmd_band(args) -> int:
    """h18: a portfolio no-trade band whose width is the name's own transaction cost.

    Always runs the no-band control alongside, because the band's whole claim is a
    *difference* and a banded number on its own says nothing about what the band did.
    """
    from .costs.india import Segment
    from .evaluation import band
    seg = Segment(args.segment)
    with Database(load()).connect() as con:
        for feature in args.feature:
            row = {}
            for banded in (False, True):
                r = band.run(con, feature, args.horizon, positions=args.positions,
                             banded=banded, direction=args.direction, segment=seg,
                             start=args.start, end=args.end)
                row[banded] = r
                if not r["dates"]:
                    print(f"{feature}: no rebalances with outcomes")
                    continue
                print(f"{feature:<18} {'band' if banded else 'plain':<6} "
                      f"n {r['dates']:>4}  gross {r['mean_excess_gross']:+.4%}  "
                      f"net {r['mean_excess']:+.4%}  turn {r['turnover']:.2%}  "
                      f"cost {r['cost_per_period']:.4%}  t {r['t']:+.2f}")
            a, b = row.get(False), row.get(True)
            if a and b and a["dates"] and b["dates"]:
                print(f"{'':18} {'delta':<6} "
                      f"{'':7}gross {b['mean_excess_gross'] - a['mean_excess_gross']:+.4%}  "
                      f"net {b['mean_excess'] - a['mean_excess']:+.4%}  "
                      f"turn {b['turnover'] - a['turnover']:+.2%}  "
                      f"cost {b['cost_per_period'] - a['cost_per_period']:+.4%}  "
                      f"t {b['t'] - a['t']:+.2f}")
                mix = ", ".join(f"{k} {v:.0%}" for k, v in b["bucket_mix"].items()
                                if v > 0.005)
                print(f"{'':18} holdings: {mix}")
    return 0


def cmd_kite(args) -> int:
    """Kite Connect: the daily login, and a read-only look at live data."""
    import os
    import webbrowser

    from .providers import kite_data as kd
    from .providers import kite_login as kl

    if args.action == "login":
        from .providers.kite_login_server import Receiver
        url = kl.login_url()
        # The port is claimed BEFORE the browser is opened. Opening first leaves a window
        # where another process could hold the port and receive the request token.
        with Receiver(port=args.port) as rx:
            print("Opening Kite login in your browser.")
            print("You authenticate with Zerodha directly - nothing here sees your "
                  "password.")
            print()
            print(f"  {url}")
            print()
            print(f"Listening on {rx.url} (this machine only).")
            try:
                webbrowser.open(url)
            except Exception:                  # noqa: BLE001 - headless is fine
                print("(could not open a browser; paste the URL above yourself)")
            request_token = rx.wait()
        session = kl.exchange(request_token)
        path = kl.save(session)
        who = session.get("user_id") or "?"
        print(f"Access token saved for today ({who}) -> {path}")
        print("Kite rotates tokens each morning, so this is a daily step.")
        return 0

    if args.action == "status":
        from . import env as fbenv
        st = fbenv.status()
        where = st["path"] or "(no .env found)"
        print(f"env file     {where}")
        if st["blank"]:
            print("             still blank: " + ", ".join(st["blank"]))
        token = kl.stored_token()
        print(f"api key      {'set' if os.environ.get('KITE_API_KEY') else 'NOT SET'}")
        print(f"api secret   {'set' if os.environ.get('KITE_API_SECRET') else 'NOT SET'}")
        print(f"access token {'valid for today' if token else 'absent or stale'}")
        print(f"token file   {kl.token_path()}")
        print("\nread-only endpoints this build can reach:")
        for e in kd.ENDPOINTS:
            print(f"   GET {e}")
        print("no order endpoint exists in this package (see tests/test_phase3_kite.py)")
        return 0

    if args.action == "quote":
        if not args.symbol:
            print("give at least one symbol, e.g. --symbol NSE:RELIANCE")
            return 2
        data = kd.quote(args.symbol, kind=args.kind)
        for sym, q in data.items():
            ohlc = q.get("ohlc") or {}
            print(f"{sym:<22} last {q.get('last_price', '-'):>10}  "
                  f"o {ohlc.get('open', '-')}  h {ohlc.get('high', '-')}  "
                  f"l {ohlc.get('low', '-')}  c {ohlc.get('close', '-')}")
        return 0

    if args.action == "candles":
        if not args.symbol:
            print("give one symbol, e.g. --symbol NSE:RELIANCE")
            return 2
        sym = args.symbol[0].split(":")[-1]
        token = kd.token_for(sym)
        if token is None:
            print(f"no instrument token for {sym}")
            return 1
        start = _d(args.start) if args.start else date.today()
        as_of = _d(args.date) if args.date else date.today()
        rows = kd.candles(token, start, as_of, interval=args.interval)
        print(f"{sym}: {len(rows)} {args.interval} candles, {start} .. {as_of}")
        for r in rows[:args.limit]:
            print(f"  {r['ts']}  o {r['open']}  h {r['high']}  l {r['low']}  "
                  f"c {r['close']}  v {r['volume']}")
        return 0
    return 2


def cmd_research(args) -> int:
    """The published-anomaly catalogue, and what of it this project can reach."""
    from . import research as R
    if args.blocked:
        print("missing inputs, ranked by how many strategies they block:")
        for need, names in R.blocked_by_data().items():
            print(f"  {need:<22} {len(names):>2}  {', '.join(names[:4])}"
                  + (" ..." if len(names) > 4 else ""))
        return 0
    if args.family:
        rows = R.by_family(args.family)
    elif args.ready:
        rows = R.by_status(R.READY)
    else:
        rows = R.ALL
    for f in rows:
        mark = {"ready": " ", "needs-fundamentals": "F",
                "needs-external": "X"}[f["status"]]
        print(f"{mark} {f['name']:<26} {f['family']:<17} {f['source']}")
        if args.verbose:
            print(f"    {f['claim']}")
            if f["prior"]:
                print(f"    -> {f['prior']}")
    s = R.summary()
    print(f"\n{s['total']} strategies, {s['sources']} papers.  "
          f"{s['ready']} ready, {s['needs_fundamentals']} need fundamentals (F), "
          f"{s['needs_external']} need external data (X)")
    return 0


def cmd_safety(args) -> int:
    """Is now a sensible time to act, and is this name safe to act on?"""
    from .decisions import safety
    with Database(load()).connect() as con:
        d = _d(args.date) if args.date else None
        w = safety.window(con, as_of=d)
        print(f"market window {w['as_of']}: "
              + ("clear" if w["clear"] else f"NOT clear - {w['why']}"))
        if args.isin:
            a = safety.assess(con, isin=args.isin, as_of=d,
                              position_inr=args.position)
            print(f"{args.isin}: " + ("safe to trade" if a.safe else "REFUSED"))
            for b in a.breaches:
                print(f"    {'blocks' if b.blocking else 'notes '} {b.check}: {b.detail}")
        if not w["clear"]:
            print(f"next review: {safety.next_review(con, as_of=d)}")
    return 0


def cmd_control(args) -> int:
    """Committee-selected trades against the same universe bought blindly."""
    from .evaluation import control
    with Database(load()).connect() as con:
        for line in control.lines(control.compare(con)):
            print(line)
    return 0


def cmd_lessons(args) -> int:
    """What the closed trades support, and what they do not yet support."""
    from .decisions import postmortem
    with Database(load()).connect() as con:
        print(postmortem.record_all(con))
        found = postmortem.lessons(con)
        if not found:
            print("no closed trades to learn from yet")
            return 0
        confirmed = [x for x in found if x.confirmed()]
        print(f"{len(confirmed)} confirmed lesson(s) of {len(found)} candidates "
              f"(needs {postmortem.MIN_SUPPORT}+ trades and a "
              f"{postmortem.MATERIAL:.0%} gap):")
        for x in found[:12]:
            mark = "RULE " if x.confirmed() else "     "
            print(f"  {mark}{x.describe()}")
    return 0


def cmd_replay(args) -> int:
    """Replay the committee over past sessions so its calls can be scored."""
    from .evaluation import replay, scorecard
    cfg = load()
    dates = [_d(x) for x in args.dates]
    with Database(cfg).connect() as con:
        out = replay.run(con, dates, per_day=args.per_day)
        for s in out["sessions"]:
            print(f"{s['date']}  drafted {s['drafted']}/{s['considered']}  "
                  f"traded {s['traded']}")
            for note in s["notes"]:
                print(f"    {note[:140]}")
        print(f"closed at horizon: {out['closed']}")
        print()
        for line in scorecard.lines(scorecard.build(con)):
            print(line)
    return 0


def cmd_promote(args) -> int:
    """Walk drafts toward paper, and say where each one stops."""
    from .decisions import promote
    with Database(load()).connect() as con:
        out = promote.run(con)
        print(f"{out['considered']} live drafts: {out['reached_paper']} reached paper, "
              f"{out['traded']} opened a paper trade, {out['awaiting_price']} awaiting "
              f"the next session's price, {len(out['blocked'])} blocked, "
              f"{out['not_tradeable']} not tradeable")
        for b in out["blocked"]:
            print(f"  {b['decision_id']} stopped at {b['state']}: {b['why'][:150]}")
    return 0


def cmd_scorecard(args) -> int:
    """How the system's own calls have actually done."""
    from .evaluation import scorecard
    with Database(load()).connect() as con:
        card = scorecard.build(con, since=_d(args.since) if args.since else None)
        for line in scorecard.lines(card):
            print(line)
    return 0


def cmd_results(args) -> int:
    """Parse the results PDFs filed on a day into as-reported financials."""
    from .ingest import results as ing
    cfg = load()
    with Database(cfg).connect() as con:
        print(ing.read_day(con, cfg, _d(args.date), limit=args.limit))
        for r in con.execute("""SELECT company, basis, period_end, revenue, pat
                FROM financial_results ORDER BY observed_at DESC LIMIT 12""").fetchall():
            rev = f"{(r[3] or 0) / 10 ** 7:,.0f}" if r[3] else "-"
            pat = f"{(r[4] or 0) / 10 ** 7:,.0f}" if r[4] else "-"
            print(f"  {r[0][:30]:<30} {r[1]:<13} {r[2]}  revenue Rs {rev} cr, PAT Rs {pat} cr")
    return 0


def cmd_results_bulk(args) -> int:
    """Parse results PDFs for the most liquid names across a date range (resumable)."""
    from .ingest import results as ing
    cfg = load()
    with Database(cfg).connect() as con:
        universe = {r[0] for r in con.execute("""
            SELECT l.isin FROM (SELECT lineage, AVG(adv20) a FROM features
                                WHERE business_date >= DATE '2025-01-01' GROUP BY 1
                                ORDER BY a DESC LIMIT ?) t
            JOIN security_lineage l USING (lineage)""", [args.names]).fetchall()}
        print(f"{len(universe)} ISINs in the top {args.names} by recent turnover", flush=True)
        stats = ing.read_bulk(con, cfg, universe=universe, start=_d(args.start), end=_d(args.end),
                              limit=args.limit,
                              on_progress=lambda i, n, st: print(f"  {i}/{n} {st}", flush=True))
        print(stats)
        print(con.execute("SELECT COUNT(*), COUNT(DISTINCT isin) FROM financial_results").fetchone())
    return 0


def cmd_paper_daily(args) -> int:
    """The daily paper loop: scan the market, remember every decision, resolve what has matured."""
    from .paper import daily
    with Database(load()).connect() as con:
        r = daily.run_daily(con, as_of=_d(args.date) if args.date else None)
    print(f"{r['as_of']}: {r['names_considered']} names -> {r['by_disposition']}")
    print(f"best net alpha {r['best_net_alpha']:+.2%} against a {r['hurdle']:.2%} hurdle")
    for p in r["paper_book"]:
        print(f"  PAPER {p['ticker']:<14} weight {p['weight']:.1%}  net 20d {p['net_alpha_20d']:+.2%}")
    if r["nothing_clears"]:
        print("  nothing clears costs today - the paper book is empty, which is an answer")
    print(f"resolved: {r['resolved']}")
    return 0


def cmd_plan(args) -> int:
    """Size, horizon and exits for one name and one style, with the evidence for the style stated first."""
    from .decisions import plan as P
    from .opportunity import engine as E
    with Database(load()).connect() as con:
        asof = con.execute("SELECT MAX(business_date) FROM features").fetchone()[0]
        row = con.execute("""
            SELECT p.ticker, p.close_adj, f.vol_60, f.adv20, f.atr_14_pct FROM features f
            JOIN adjusted_prices p ON p.lineage = f.lineage AND p.business_date = f.business_date
            WHERE f.business_date = ? AND UPPER(p.ticker) = ?""", [asof, args.ticker.upper()]).fetchone()
        if not row:
            print(f"{args.ticker}: no liquid-universe row on {asof}")
            return 1
        ticker, price, vol, adv, atr = row
        rt = E.decide  # noqa: F841 - the cost bucket below mirrors the engine's liquidity buckets
        bucket = "mega" if adv >= 5e9 else "large" if adv >= 1e9 else "mid" if adv >= 2e8 else "small"
        p = P.plan(ticker=ticker, price=float(price), vol_annual=float(vol), adv_inr=float(adv),
                   capital_inr=args.capital, style=args.style, risk_per_trade=args.risk,
                   atr_pct=float(atr) if atr else None, as_of=asof, bucket=bucket,
                   side=args.side)
    print(P.render(p))
    return 0


def cmd_costs_calc(args) -> int:
    """Itemised cost of a trade, with the impact range and the tax on a hypothetical gain."""
    from .costs import calculator as CC
    from .costs.india import Segment
    from .costs.tax import TaxRules
    adv = vol = None
    if args.ticker:
        with Database(load()).connect() as con:
            asof = con.execute("SELECT MAX(business_date) FROM features").fetchone()[0]
            row = con.execute("""SELECT f.adv20, f.vol_60 FROM features f JOIN adjusted_prices p
                                 ON p.lineage = f.lineage AND p.business_date = f.business_date
                                 WHERE f.business_date = ? AND UPPER(p.ticker) = ?""",
                              [asof, args.ticker.upper()]).fetchone()
        if not row:
            print(f"{args.ticker}: not in the liquid universe on {asof}")
            return 1
        adv, vol = float(row[0]), float(row[1])
        print(f"{args.ticker.upper()}: turnover Rs {adv / 1e7:,.0f} cr/day, volatility {vol:.0%} (as of {asof})")
    q = CC.quote(order_inr=args.order, segment=Segment(args.segment), adv_inr=adv, vol_annual=vol,
                 hold_sessions=args.hold, expected_gross_return=args.gain,
                 rules=TaxRules(slab=args.slab))
    print(CC.render(q))
    if args.table:
        print()
        print("COST OF ONE ROUND TRIP BY ORDER SIZE (a liquid large-cap)")
        for r in CC.account_table(segment=Segment(args.segment)):
            print(f"  Rs {r['order']:>11,.0f}: charges {r['fees_bp']:5.1f} bp + impact {r['impact_bp_low']:4.1f}+ bp = "
                  f"{r['total_bp_low']:5.1f}-{r['total_bp_high']:5.1f} bp  (Rs {r['total_inr_low']:,.0f}+)")
    return 0


def cmd_paper_intraday(args) -> int:
    """Intraday paper trading: live from Kite quotes, a replay of a past day, or the evidence so far."""
    import logging
    from .paper import intraday_runner as IR
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if args.report:
        con = IR.ledger()
        for e in IR.evidence(con):
            print(f"{e['strategy']:4s} {e['mode']:6s} {e['trades']:5d} trades / {e['days']:3d} days  net "
                  f"{e['net_bp_per_trade']:+6.1f} bp/trade (se {e['se_bp']:4.1f})  win {e['win_rate']:.0%}  "
                  f"net Rs {e['net_inr']:>10,.0f}  backtest {e['backtest_bp']}  -> {e['status']}")
        return 0
    if args.replay:
        out = IR.run_replay(_d(args.replay), n=args.names)
    else:
        out = IR.run_live(n=args.names)
    print(out)
    return 0


def cmd_champion(args) -> int:
    """Record each strategy's picks after the lock, resolve matured cohorts, and print the standings."""
    import duckdb
    from .evaluation import champion as C
    from .paper import intraday_runner as IR
    led = IR.ledger()
    rcon = duckdb.connect(str(load().db_path), read_only=True)
    try:
        day = rcon.execute("SELECT MAX(business_date) FROM features").fetchone()[0]
        if args.record:
            if day < C.LOCK_DATE:
                print(f"latest features {day} are before the lock {C.LOCK_DATE}: nothing recorded")
            else:
                print("recorded", day, C.record(rcon, led, day))
        print("resolved cohorts:", C.resolve(led, rcon))
    finally:
        rcon.close()
    for s in C.standings(led):
        print(f"  {s['strategy']:20s} {s['status']:22s} cohorts {s.get('cohorts', 0):3d}"
              + (f"  net vs champion {s['mean_net_vs_champion']:+.2%} (t {s['t']:+.2f})" if s.get("cohorts") else ""))
    return 0


def cmd_fno_snapshot(args) -> int:
    """Evening F&O snapshot: futures basis and open interest for every F&O stock, option-chain statistics for the
    most liquid ones, and day-over-day positioning. Read-only Kite calls; stored in the paper ledger file."""
    from datetime import date as _date
    from .ingest import kite_fno as F
    from .paper import intraday_runner as IR
    from .providers import kite_data as kd
    inst = kd.instruments("NFO")
    names = sorted({(r.get("name") or "").upper() for r in inst if r.get("instrument_type") == "FUT"
                    and r.get("segment") == "NFO-FUT" and (r.get("name") or "").isalpha()} - {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT"})
    rows = F.futures_snapshot(set(names), instrument_list=inst, quote_fn=kd.quote)
    ranked = sorted(rows, key=lambda s: -rows[s]["fut_oi"] * rows[s]["fut"])[:args.chains]
    for s in ranked:
        try:
            sm = F.summarise(F.chain(s, spot=rows[s]["spot"], instrument_list=inst))
            rows[s].update({k: sm.get(k) for k in ("atm_iv", "skew_5pct", "pcr_oi", "max_pain")})
        except Exception as e:                       # noqa: BLE001 - one bad chain is not the snapshot
            print(f"  {s}: no chain ({str(e)[:60]})")
    day = _date.today().isoformat()
    con = IR.ledger()
    print(f"snapshot {day}: {F.save_snapshot(con, day, rows)} futures, {len(ranked)} option chains")
    pos = [p for p in F.positioning(con, day) if p["buildup"]]
    from collections import Counter
    print("positioning vs the previous snapshot:", dict(Counter(p["buildup"] for p in pos)) or "no previous snapshot yet")
    return 0


def cmd_terminal(args) -> int:
    """Write the trader's terminal page for the latest session (data/terminal/index.html)."""
    import datetime as _dt
    from pathlib import Path as _P
    import duckdb
    import pandas as pd
    from .evaluation import setup_analogues as SA
    from .paper import intraday_runner as IR
    from .paper import terminal as T
    rcon = duckdb.connect(str(load().db_path), read_only=True)
    try:
        cache = _P("data") / "analogue_pool.parquet"
        if cache.exists():
            pool = pd.read_parquet(cache)
        else:
            print("building the analogue pool once (a few minutes)...")
            pool = SA.build_pool(rcon, start=_dt.date(2016, 6, 1), end=_dt.date.today(), step=5)
            pool.to_parquet(cache)
        from .paper import book as BK
        bcon = duckdb.connect(str(BK.PATH), read_only=True) if BK.PATH.exists() else None
        try:
            path = T.write(T.build(rcon, IR.ledger(), analogue_pool=pool, book=bcon))
        finally:
            if bcon is not None:
                bcon.close()
    finally:
        rcon.close()
    print(f"terminal written: {path.resolve()}")
    return 0


def cmd_book(args) -> int:
    """Suggestion paper books: the terminal's names bought and sold on paper by their pre-registered rules."""
    import datetime as _dt
    import duckdb
    from .paper import book as BK
    specs = list(BK.BOOKS.values()) if args.book == "all" else [BK.BOOKS[args.book]]
    rcon = duckdb.connect(str(load().db_path), read_only=True)
    book = BK.connect()
    try:
        for spec in specs:
            if args.replay:
                end = _dt.date.fromisoformat(args.end) if args.end else None
                n = BK.replay(rcon, book, _dt.date.fromisoformat(args.replay), end, spec=spec, mode=args.label)
                print(f"{spec.version} {args.label} from {args.replay}: {n} positions taken")
            elif not args.report:
                r = BK.live(rcon, book, spec=spec)
                print(f"{spec.version} live for {r['day']}: {r['added']} new position(s) to buy at the next close")
            print(BK.render(book, spec) + "\n")
    finally:
        book.close()
        rcon.close()
    return 0


def cmd_loss_review(args) -> int:
    """Which situations keep losing in the intraday paper record; losing ones become PROPOSED hypotheses."""
    import duckdb
    from .paper import intraday_runner as IR
    from .paper import loss_review as L
    try:
        con = IR.ledger()
    except duckdb.IOException:
        print("the paper ledger is open in the live run; try again after 15:20")
        return 1
    try:
        r = L.review(L.frame(con, args.mode))
        print(L.render(r))
        if args.propose:
            print(f"\n{L.propose(con, r)} hypothesis(es) written as PROPOSED - nothing changes until one is pre-registered and tested")
    finally:
        con.close()
    return 0


def cmd_preregs(args) -> int:
    """Audit every pre-registration against git: written before its result, never changed after it."""
    from .research import preregistry as PR
    rows = PR.audit()
    print(PR.render(rows))
    return 1 if any(r["status"] in PR.VIOLATIONS for r in rows) else 0


def cmd_data_contracts(args) -> int:
    """Each source's contract (source, time carried, revision policy, limits) and whether it is met today."""
    import duckdb
    from . import data_contracts as DC
    rcon = duckdb.connect(str(load().db_path), read_only=True)
    try:
        rows = DC.check(rcon)
    finally:
        rcon.close()
    for r in rows:
        print(f"{'OK  ' if r['ok'] else 'FAIL'} {r['table']:20s} {r.get('rows', 0):>11,} rows  {str(r.get('first', ''))[:10]} .. "
              f"{str(r.get('last', ''))[:19]}  {r['why']}")
        if r.get("limits"):
            print(f"      {r['source']}; time: {r['times']}; revisions: {r['revisions']}; limits: {r['limits']}")
    return 0 if all(r["ok"] for r in rows) else 1


def cmd_reproduce(args) -> int:
    """Recreate a recorded trial: fingerprint its inputs, then run its script (the ledger is never written)."""
    import json
    import duckdb
    from .evaluation import standard as S
    from .research import reproduce as RP
    if args.list or not args.name:
        rec = {r["name"]: r for r in S.ledger() if r.get("kind") == "trial"}
        for n, (script, _) in RP.TRIALS.items():
            print(f"  {n:58s} {script:30s} {rec.get(n, {}).get('verdict', '-')}")
        return 0
    if args.name not in RP.TRIALS:
        print(f"no script for {args.name!r}; fb reproduce --list")
        return 2
    rcon = duckdb.connect(str(load().db_path), read_only=True)
    try:
        m = RP.manifest(rcon, args.name)
    finally:
        rcon.close()
    print(json.dumps(m, indent=1))
    if args.fingerprint_only:
        return 0
    return RP.run_script(args.name)


def cmd_stress(args) -> int:
    """Stress your Kite holdings: India's own bad episodes replayed on the names, plus market, sector and liquidity shocks."""
    import duckdb
    from .portfolio import my_book as MB
    from .portfolio import stress as ST
    con = MB.connect(read_only=True)
    try:
        h = con.execute("""SELECT tradingsymbol, quantity, last_price FROM holdings_snap
                           WHERE day = (SELECT MAX(day) FROM holdings_snap) AND quantity > 0""").df()
    finally:
        con.close()
    if h.empty:
        print("no holdings stored yet (fb my-portfolio --sync after the Kite login)")
        return 0
    rcon = duckdb.connect(str(load().db_path), read_only=True)
    try:
        asof = rcon.execute("SELECT MAX(business_date) FROM features").fetchone()[0]
        book = ST.book_from_holdings(rcon, h, asof)
        prices, index = ST.load_inputs(rcon, list(book["ticker"]))
    finally:
        rcon.close()
    r = ST.run(book, prices, index)
    print(f"book Rs {r['book_inr']:,.0f}")
    for k, v in r["scenarios"].items():
        extra = f" (proxied by beta: {', '.join(v['proxied'])})" if v.get("proxied") else ""
        print(f"  {k:18s} {v['share'] * 100:+6.1f}%  Rs {v['pnl_inr']:+,.0f}{extra}")
    lq = r["liquidation_sessions"]
    print(f"  liquidity: worst {lq['worst']:.1f} sessions to exit ({lq['worst_name']}) at 10% of a halved ADV; "
          f"{lq['share_out_in_1_session']:.0%} of the book out in one session")
    print(f"  not modelled: {'; '.join(r['not_modelled'])}")
    return 0


def cmd_my_portfolio(args) -> int:
    """Your Kite holdings (read only): thesis health, book risk, better alternatives. Information, not advice."""
    import datetime as _dt
    import duckdb
    from .portfolio import my_book as MB
    con = MB.connect()
    try:
        if args.sync:
            from .providers import kite_account as KA
            n = MB.sync(con, _dt.date.today(), holdings_fn=KA.holdings, positions_fn=KA.positions, trades_fn=KA.trades)
            print(f"synced from Kite (read only): {n}")
        bk = MB.latest(con)
        if bk.empty:
            print("no holdings stored - run with --sync after logging in to Kite")
            return 0
        rcon = duckdb.connect(str(load().db_path), read_only=True)
        try:
            from .opportunity import scanners as S
            asof = rcon.execute("SELECT MAX(business_date) FROM features").fetchone()[0]
            best = S.best_per_name(S.scan(S.cross_section(rcon, asof), asof)).sort_values("expected_net", ascending=False).head(10)
            a = MB.analyse(rcon, bk, asof, candidates=best)
            entries = MB.record_entries(con, bk, a["composite_pct"], asof)
            a = MB.analyse(rcon, bk, asof, candidates=best, entries=entries)
        finally:
            rcon.close()
        rk = a["risk"]
        print(f"book Rs {rk['value']:,.0f}, P&L Rs {rk['pnl']:,.0f}; volatility {rk['vol']:.0%} a year, beta {rk['beta']:.2f}; "
              f"{rk['at_stops']:.1%} of the book at risk to the stops; largest sector {rk['top_sector']}")
        for h in a["holdings"]:
            print(f"  {h['symbol']:12s} {h['since_cost'] * 100:+6.1f}% since cost  health {h['score']:3.0f}  {h['recommendation']:13s} {h['reason']}")
            x = h["exit"]
            print(f"      exit brain: {x['action']}" + (f" {x['reduce_fraction']:.0%}" if x["action"] == "REDUCE" else "")
                  + f" ({'tested rule' if x['tested_basis'] else 'rule of thumb'}): "
                  + "; ".join(f"{v['view']} {v['action']}" for v in x["views"]))
        if a["alternatives"] is not None:
            print("\nwhere the next unit of risk goes furthest (alpha per unit of risk added to this book):")
            for r in a["alternatives"].head(5).itertuples():
                print(f"  {r.ticker:12s} net {r.alpha * 100:+.2f}%  correlation with your book {r.corr_to_book:+.2f}")
    finally:
        con.close()
    return 0


def cmd_journal(args) -> int:
    """Your round trips from the Kite trade book, each with an autopsy; a report once there are enough."""
    import duckdb
    import pandas as pd
    from .portfolio import journal as J
    from .portfolio import my_book as MB
    con = MB.connect()
    try:
        if getattr(args, "import_path", None):
            from pathlib import Path as _P
            from .portfolio import tradebook_import as TI
            print(TI.load(con, TI.parse(_P(args.import_path).read_text(encoding="utf-8"))))
        t = con.execute("SELECT trade_id, ts, day, tradingsymbol, product, side, quantity, price FROM my_trades").df()
    finally:
        con.close()
    trips = J.round_trips(t) if len(t) else t
    print(f"{len(t)} trade(s) in the journal, {len(trips)} closed round trip(s). Kite returns only the current day's "
          "trade book, so the journal grows from each evening's refresh.")
    if len(trips):
        rcon = duckdb.connect(str(load().db_path), read_only=True)
        try:
            at = J.attribute(rcon, trips)
        finally:
            rcon.close()
        for r in at.itertuples():
            if getattr(r, "attributed", False):
                print(f"  {r.symbol:12s} {r.side:5s} {r.entry_day}->{r.exit_day} net {r.net * 100:+.2f}%  market {r.market * 100:+.2f}  "
                      f"sector {r.sector * 100:+.2f}  selection {r.selection * 100:+.2f}  entry {r.entry * 100:+.2f}  exit {r.exit * 100:+.2f}  costs {r.costs * 100:+.2f}")
        rep = J.report(at[at["attributed"]] if "attributed" in at else at)
        print(f"report: {rep}")
        from .portfolio import behaviour_review as BRV
        rcon = duckdb.connect(str(load().db_path), read_only=True)
        try:
            def closes(sym):
                d = rcon.execute("SELECT business_date, close_adj FROM adjusted_prices WHERE ticker = ? ORDER BY business_date",
                                 [sym]).df()
                return d.set_index(pd.to_datetime(d["business_date"]))["close_adj"] if len(d) else None
            ev = {}
            for sym, day in rcon.execute("""SELECT DISTINCT p.ticker, a.business_date FROM announcements a
                    JOIN (SELECT DISTINCT isin, ticker FROM adjusted_prices) p USING (isin)
                    WHERE a.materiality = 'high' AND p.ticker IN (SELECT UNNEST(?))""", [list(set(trips["symbol"]))]).fetchall():
                ev.setdefault(sym, set()).add(day)
            rv = BRV.review(t, at.assign(net=at["net"]) if "net" in at else trips, closes, events=ev)
        finally:
            rcon.close()
        print(f"habits: {rv['tag_counts']}; days over {BRV.MAX_TRADES_DAY} fills: {rv['overtrading_days']}; "
              f"averaging down: {len(rv['averaging_down'])}")
        ap = rv["anti_playbook"]
        print(f"anti-playbook ({ap.get('note', '')}):")
        for e in ap["entries"]:
            print(f"  AVOID {e['tag']}: {e['trips']} trips, mean net {e['mean_net'] * 100:+.2f}%, t {e['t']:.1f}")
        if not ap["entries"]:
            print("  nothing yet - it needs at least 8 trips of a habit losing at t <= -2")
        try:
            from .evaluation import regret as RG
            from .paper import intraday_runner as IR
            led = IR.ledger()
            lists = led.execute("SELECT day, rank, ticker, net FROM terminal_lists").df()
            led.close()
            rcon = duckdb.connect(str(load().db_path), read_only=True)
            try:
                jv = RG.journal_vs_list(trips, lists, rcon)
            finally:
                rcon.close()
            for r in jv.itertuples():
                print(f"  you chose {r.symbol} ({r.trip * 100:+.2f}%); the list of {r.list_day} averaged {r.list_mean * 100:+.2f}% over the same "
                      f"days and its best, {r.list_best}, did {r.list_best_ret * 100:+.2f}%" + (" - it was on the list" if r.on_list else ""))
        except Exception as e:                          # noqa: BLE001 - no lists stored yet
            print(f"  (no terminal list to compare with yet: {str(e)[:60]})")
    return 0


def cmd_alerts(args) -> int:
    """Raise desktop alerts for anything worth acting on (sent once each; nothing leaves this machine)."""
    import datetime as _dt
    import duckdb
    from . import alerts as AL
    from .opportunity import scanners as S
    from .paper import intraday_runner as IR
    from .portfolio import my_book as MB
    from .risk import killswitch as KS
    day = _dt.date.today().isoformat()
    out = []
    rcon = duckdb.connect(str(load().db_path), read_only=True)
    try:
        asof = rcon.execute("SELECT MAX(business_date) FROM features").fetchone()[0]
        best = S.best_per_name(S.scan(S.cross_section(rcon, asof), asof))
        out += AL.opportunities(best, str(asof))
        if MB.PATH.exists():
            con = MB.connect()
            try:
                bk = MB.latest(con)
            finally:
                con.close()
            if not bk.empty:
                out += AL.holdings(MB.analyse(rcon, bk, asof)["holdings"], day)
    finally:
        rcon.close()
    try:
        led = IR.ledger()
        row = led.execute("SELECT status FROM runs WHERE day = ? AND mode = 'live'", [day]).fetchone()
        led.close()
    except Exception:                                   # noqa: BLE001
        row = None
    is_weekday = _dt.date.today().weekday() < 5
    out += AL.system(day=day, killswitch_level=KS.load().get("level", "OK"),
                     run_status=(row[0] if row else ("missing" if is_weekday else None)))
    sent = AL.send(out) if not args.dry_run else []
    for a in out:
        print(f"{'SENT ' if a in sent else 'held '} {a['title']} - {a['body']}")
    if not out:
        print("nothing to alert")
    return 0


def cmd_lifecycle(args) -> int:
    """One opportunity end to end: found, taken or rejected (and why), sold, resolved."""
    import duckdb
    from .opportunity import lifecycle as LC
    from .paper import book as BK
    from .paper import intraday_runner as IR
    led = IR.ledger()
    bk = duckdb.connect(str(BK.PATH), read_only=True) if BK.PATH.exists() else None
    try:
        print(LC.render(args.ticker, args.day, LC.timeline(args.ticker, args.day, ledger=led, book=bk)))
    finally:
        led.close()
        if bk is not None:
            bk.close()
    return 0


def cmd_daily_results(args) -> int:
    """The day's paper results in one report (data/paper_reports/DATE-results.md)."""
    import datetime as _dt
    import duckdb
    from .paper import book as BK
    from .paper import daily_results as DR
    from .paper import intraday_runner as IR
    day = args.day or _dt.date.today().isoformat()
    led = duckdb.connect(str(IR.LEDGER), read_only=True)
    bk = duckdb.connect(str(BK.PATH), read_only=True) if BK.PATH.exists() else None
    try:
        text = DR.build(led, day, book=bk)
    finally:
        led.close()
        if bk is not None:
            bk.close()
    print(text)
    print(f"written: {DR.write(text, day)}")
    return 0


def cmd_zoo(args) -> int:
    """The live paper zoo: every tested daily strategy on paper, rules frozen as tested."""
    import duckdb
    from .paper import intraday_runner as IR
    from .paper import zoo as Z
    rcon = duckdb.connect(str(load().db_path), read_only=True)
    try:
        led = duckdb.connect(str(IR.LEDGER), read_only=True)
        fno = sorted({r[0] for r in led.execute("SELECT DISTINCT symbol FROM fno_snapshots").fetchall()})
        snap = led.execute("""SELECT day, symbol, spot, fut, fut_expiry, basis_annualised FROM fno_snapshots
                              WHERE day >= CAST(current_date - INTERVAL 10 DAY AS VARCHAR)""").df()
        led.close()
    except Exception:                                   # noqa: BLE001 - the ledger is busy in market hours
        fno, snap = [], None
    if not fno:
        try:
            h = duckdb.connect("data/futures_history.duckdb", read_only=True)
            fno = sorted({r[0] for r in h.execute("SELECT DISTINCT symbol FROM futures_daily").fetchall()} - {"NIFTY", "BANKNIFTY"})
            h.close()
        except Exception:                               # noqa: BLE001 - no F&O list at all: no pairs or stat-arb tonight
            fno = []
    z = Z.connect()
    try:
        if not args.report:
            print(Z.run(rcon, z, fno_symbols=fno, fno_snap=snap))
        print(Z.summary(z).round(5).to_string())
    finally:
        z.close()
        rcon.close()
    return 0


def cmd_filings(args) -> int:
    """Read the documents a day's material filings point at, and mint what they say."""
    from .ingest import filings
    cfg = load()
    with Database(cfg).connect() as con:
        print(filings.read_day(con, cfg, _d(args.date), limit=args.limit))
        for company, kind, raw, ctx in con.execute("""
                SELECT a.company, f.kind, f.raw, LEFT(f.context, 90)
                FROM filing_facts f JOIN announcements a USING (news_id)
                WHERE a.business_date = ? ORDER BY f.extracted_at DESC LIMIT 15""",
                                                   [_d(args.date)]).fetchall():
            print(f"  {company[:28]:<28} {kind:<20} {raw[:24]:<24} {ctx}")
    return 0


def cmd_security(args) -> int:
    """Attempts by untrusted text to steer a model, and where they were seen."""
    cfg = load()
    with Database(cfg).connect() as con:
        rows = con.execute("""SELECT detected_at, where_seen, pattern, action, subject,
                LEFT(matched, 60) FROM security_findings
                ORDER BY detected_at DESC LIMIT ?""", [args.limit]).fetchall()
        if not rows:
            print("no findings recorded")
            return 0
        for when, where, pattern, action, subject, matched in rows:
            print(f"{str(when)[:19]}  {where:<22} {pattern:<22} {action}")
            print(f"    {subject}: {matched}")
        by = con.execute("""SELECT pattern, COUNT(*) FROM security_findings
                            GROUP BY 1 ORDER BY 2 DESC""").fetchall()
        print()
        print("totals: " + ", ".join(f"{p} {n}" for p, n in by))
    return 0


def cmd_monitor(args) -> int:
    """Re-check every live decision's invalidation conditions."""
    from datetime import date as _date

    from .decisions import monitor
    cfg = load()
    with Database(cfg).connect() as con:
        d = _d(args.date) if args.date else None
        if args.dry_run:
            for did in monitor.live_decisions(con):
                ev = monitor.evaluate(con, did, d or _date.today())
                print(f"{did}  {ev['isin']}")
                for r in ev["results"]:
                    print(f"   {r['state']:<13} {r['spec'].get('check','?'):<16} {r['detail']}")
                for u in ev["unmonitored"]:
                    print(f"   unmonitored   (prose only)     {u[:80]}")
            return 0
        out = monitor.run(con, d)
        print(f"checked {out['checked']} live decisions; {out['triggered']} triggered; "
              f"{out['unmonitored_conditions']} conditions have no typed check")
        for a in out["alerts"]:
            print(f"  ALERT {a['isin']} {a['check']}: {a['detail'][:90]}")
    return 0


def cmd_fundamentals(args) -> int:
    """Company ratios from Screener (Tier 3), cross-checked against our own close."""
    from .ingest import fundamentals
    from .providers.screener import Screener
    cfg = load()
    symbols = list(args.symbol)
    if args.watchlist:
        path = cfg.data_root / "watchlist.txt"
        if not path.exists():
            print(f"no watchlist at {path}")
            return 1
        symbols += [ln.strip().upper() for ln in path.read_text(encoding="utf-8").splitlines()
                    if ln.strip() and not ln.startswith("#")]
    provider = Screener()          # one instance: the crawl delay is per provider
    agree = disagree = nocheck = failed = 0
    with Database(cfg).connect() as con:
        for symbol in dict.fromkeys(symbols):
            try:
                r = fundamentals.fetch_company(con, cfg, symbol, provider=provider)
            except Exception as e:                  # noqa: BLE001 - one bad symbol
                failed += 1
                print(f"{symbol:<12} not fetched: {type(e).__name__}: {str(e)[:70]}")
                continue
            chk = r["price_check"]
            if chk and chk["agrees"]:
                agree += 1
                note = f"price agrees ({chk['drift']:.2%})"
            elif chk:
                disagree += 1
                note = (f"PRICE DISAGREES: ours {chk['our_close']:.2f} vs Screener "
                        f"{chk['screener_price']:.2f} ({chk['drift']:.1%})")
            else:
                nocheck += 1
                note = "no close of our own to compare"
            print(f"{r['symbol']:<12} {(r['name'] or '-')[:34]:<34} {r['ratios']:>2} ratios  {note}")
    if len(symbols) > 1:
        print()
        print(f"price cross-check: {agree} agree, {disagree} disagree, "
              f"{nocheck} uncheckable, {failed} not fetched")
    return 0


def cmd_newsfetch(args) -> int:
    """Fetch the article a filing links to, where the publisher's robots.txt allows it."""
    from .ingest import newsfetch
    cfg = load()
    with Database(cfg).connect() as con:
        print(newsfetch.fetch_day(con, cfg, _d(args.date), limit=args.limit,
                                  only_missing_headline=args.only_missing))
        for dom, title, when in con.execute("""SELECT ar.domain, ar.title, ar.published_at
                FROM news_articles ar JOIN announcement_news n ON n.url = ar.url
                JOIN announcements a USING (news_id) WHERE a.business_date = ?
                AND ar.title IS NOT NULL ORDER BY ar.fetched_at DESC LIMIT 10""",
                                            [_d(args.date)]).fetchall():
            print(f"  {dom:<20} {str(when)[:16]}  {title[:80]}")
    return 0


def cmd_mfnav(args) -> int:
    """Mutual-fund NAVs from AMFI's public daily feed."""
    from .ingest import mfnav
    cfg = load()
    with Database(cfg).connect() as con:
        print(mfnav.ingest(con, cfg, _d(args.date) if args.date else None))
        for r in con.execute("""SELECT fund_house, count(*), max(nav_date) FROM mf_nav
                GROUP BY 1 ORDER BY 2 DESC LIMIT 8""").fetchall():
            print(f"  {r[0][:38]:<38} {r[1]:6d} schemes, latest {r[2]}")
    return 0


def cmd_delivery(args) -> int:
    """NSE delivery position: --prefetch fills the lake (network, no database); otherwise load lake -> table."""
    from .ingest import delivery
    cfg = load()
    start, end = _d(args.start), _d(args.end or args.start)
    if args.prefetch:
        r = delivery.prefetch(cfg, start, end, workers=args.workers)
        print({k: (v if k != "failed" else v[:10]) for k, v in r.items()}, f"failed total {len(r['failed'])}")
        return 0 if not r["failed"] else 1
    with Database(cfg).connect() as con:
        r = delivery.load(con, cfg, start, end)
        print({k: (v if k != "date_mismatch" else v[:5]) for k, v in r.items()})
    return 0


def cmd_nse_archives(args) -> int:
    """NSE dated derivatives archives (F&O bhavcopy, ban list, participant OI/volume): --prefetch, then load."""
    from .ingest import nse_archives as NA
    cfg = load()
    start, end = _d(args.start), _d(args.end or args.start)
    kinds = [k for k in (args.kinds or ",".join(NA.KINDS)).split(",") if k]
    if args.prefetch:
        print(NA.prefetch(cfg, kinds, start, end, on_progress=lambda d, o: print(d, o, flush=True)))
        return 0
    if "fo_bhav" in kinds:
        print("fo_bhav ->", NA.FO_DB, NA.load_fo(cfg, start, end))
    if set(kinds) - {"fo_bhav"}:
        with Database(cfg).connect() as con:
            print(NA.load_small(con, cfg, start, end))
    return 0


def cmd_shareholding(args) -> int:
    """NSE shareholding patterns: --prefetch (--all for every traded symbol's history; default the last 3 days), then load."""
    from .ingest import shareholding as SH
    cfg = load()
    if args.prefetch:
        if args.all:
            with Database(cfg, read_only=True).connect() as con:
                syms = [r[0] for r in con.execute("""SELECT DISTINCT ticker FROM adjusted_prices
                        WHERE business_date >= (SELECT MAX(business_date) FROM adjusted_prices) - INTERVAL 30 DAY
                        ORDER BY 1""").fetchall()]
            print(f"{len(syms)} symbols")
            print(SH.prefetch(cfg, symbols=syms, on_progress=lambda k, o: print(k, o, flush=True)))
        else:
            a, b = SH.window()
            print(SH.prefetch(cfg, start=a, end=b))
        return 0
    with Database(cfg).connect() as con:
        print(SH.load(con, cfg))
    return 0


def cmd_bulk_deals(args) -> int:
    """NSE bulk deals, block deals and short-selling disclosures by month: --prefetch, then load."""
    from .ingest import bulk_deals as BD
    cfg = load()
    start, end = _d(args.start), _d(args.end or args.start)
    if args.prefetch:
        print(BD.prefetch(cfg, start, end))
        return 0
    with Database(cfg).connect() as con:
        print(BD.load(con, cfg, start, end))
    return 0


def cmd_index_tri(args) -> int:
    """NSE index total-return series and today's constituent lists (niftyindices.com): download and load."""
    from .ingest import index_tri as IT
    cfg = load()
    print(IT.prefetch(cfg, _d(args.start), _d(args.end) if args.end else __import__("datetime").date.today()))
    with Database(cfg).connect() as con:
        print(IT.load(con, cfg, since=_d(args.start)))
    return 0


def cmd_disclosures(args) -> int:
    """NSE structured disclosures (corporate actions, SAST Reg. 29, pledges, encumbrance, event calendar)."""
    from .ingest import nse_disclosures as ND
    cfg = load()
    ds = [k for k in (args.datasets or ",".join(ND.DATASETS)).split(",") if k]
    if args.prefetch:
        print(ND.prefetch(cfg, ds, _d(args.start), _d(args.end) if args.end else __import__("datetime").date.today()))
        return 0
    with Database(cfg).connect() as con:
        print(ND.load(con, cfg, ds, since=_d(args.since) if args.since else None))
    return 0


def cmd_kite_history(args) -> int:
    """Widen the Kite pulls (needs today's Kite login): minute bars for the top-N a year, continuous futures with OI."""
    import duckdb
    from .ingest import kite_history as KH
    from .providers import kite_data as KD
    if args.start and args.what == "futures":
        print("--start applies to minute history only; continuous-futures history uses its own date range")
        return 2
    KD.credentials()  # Refuse before opening a writable historical database.
    prog = lambda i, n, s, x: print(f"{i}/{n} {s} {x}", flush=True)     # noqa: E731
    failed = 0
    if args.what in ("minute", "all"):
        r = duckdb.connect(str(load().db_path), read_only=True)
        try:
            names = KH.minute_universe(r, args.top)
        finally:
            r.close()
        print(f"{len(names)} names (top {args.top} a year)")
        options = {"start": _d(args.start)} if args.start else {}
        result = KH.run_minute(names, on_progress=prog, **options)
        print(result)
        failed += result["failed"]
    if args.what in ("futures", "all"):
        result = KH.run_futures(on_progress=prog)
        print(result)
        failed += result["failed"]
    return 1 if failed else 0


def cmd_depth_archive(args) -> int:
    """Market hours: full quotes (5-level book, OI) for every F&O stock, near future and index options near the money,
    once a minute, into data/depth_archive.duckdb. Needs the day's Kite login; never places orders."""
    from .ingest import depth_archive as DA
    print(DA.run(until=args.until))
    return 0


def cmd_rating_actions(args) -> int:
    """Rebuild rating_actions: every CREDIT_RATING announcement with its direction (upgrade, downgrade, outlook...)."""
    import collections
    import pandas as pd
    from .events import rating_direction as RD
    with Database(load()).connect() as con:
        rows = con.execute("""SELECT news_id, isin, business_date, published_at,
                                     coalesce(headline, '') || ' ' || coalesce(subject, '') FROM announcements
                              WHERE event_type = 'CREDIT_RATING'""").fetchall()
        df = pd.DataFrame([(n, i, d, p, RD.direction(t), RD.SIGN[RD.direction(t)], t[:500]) for n, i, d, p, t in rows],
                          columns=["news_id", "isin", "business_date", "published_at", "direction", "sign", "text"])
        df = df.drop_duplicates("news_id")
        try:
            pdf = dict(con.execute("SELECT news_id, direction FROM rating_pdf_direction WHERE direction <> 'unclear'").fetchall())
        except Exception:                               # noqa: BLE001 - no letters read yet
            pdf = {}
        use = (df["direction"] == "unclear") & df["news_id"].isin(pdf)
        df.loc[use, "direction"] = df.loc[use, "news_id"].map(pdf)
        df.loc[use, "sign"] = df.loc[use, "direction"].map(RD.SIGN)
        con.register("df", df)
        con.execute("DELETE FROM rating_actions")
        con.execute("INSERT INTO rating_actions SELECT * FROM df")
        con.unregister("df")
    print(len(df), dict(collections.Counter(df["direction"])))
    return 0


def cmd_rating_docs(args) -> int:
    """Read the direction of rating actions from their letters where the headline gave none (most recent first)."""
    from .ingest import rating_docs as RDOC
    cfg = load()
    with Database(cfg).connect() as con:
        print(RDOC.run(con, cfg, limit=args.limit))
    return 0


def cmd_monthly_updates(args) -> int:
    """Index monthly business updates (auto sales, despatches, generation, AUM...) among the announcements."""
    from .ingest import monthly_updates as MU
    with Database(load()).connect() as con:
        print(MU.rebuild(con))
    return 0


def cmd_calls_backfill(args) -> int:
    """Earnings-call transcripts and investor presentations, day by day over a range (no embeddings: text only)."""
    import datetime as _dt
    from .ingest import calls as ing
    cfg = load()
    d, end = _d(args.start), _d(args.end) if args.end else _dt.date.today()
    total = {"days": 0, "read": 0, "failed": 0}
    while d <= end:
        if d.weekday() < 5:
            with Database(cfg).connect() as con:
                r = ing.read_day(con, cfg, d, limit=args.per_day, embed=False)
            total["days"] += 1
            total["read"] += r.get("read", 0)
            total["failed"] += r.get("failed", 0)
            if d.day == 1:
                print(d, total, flush=True)
        d += _dt.timedelta(days=1)
    print(total)
    return 0


def cmd_flows(args) -> int:
    """Today's FII/FPI and DII cash-market flows (NSE) and SEBI's RSS items: forward records, collected nightly."""
    from .ingest import flows as FL
    from .ingest import sebi as SB
    cfg = load()
    with Database(cfg).connect() as con:
        for name, fn in (("flows", FL.collect), ("sebi", SB.collect)):
            try:
                print(name, fn(con, cfg))
            except Exception as e:                      # noqa: BLE001 - one source failing does not stop the other
                print(name, "failed:", str(e)[:120])
    return 0


def cmd_macro(args) -> int:
    """FRED macro series (USD-INR, Brent, India 10y, call money, CPI) and MoSPI CPI/IIP: download to the lake and load."""
    from .ingest import macro as MC
    from .ingest import mospi as MO
    cfg = load()
    print(MC.prefetch(cfg))
    import datetime as _dt
    print(MO.prefetch(cfg, start_year=args.mospi_from or _dt.date.today().year - 1))
    with Database(cfg).connect() as con:
        print(MC.load(con, cfg))
        print(MO.load(con, cfg))
    return 0


def cmd_insider(args) -> int:
    """NSE structured insider disclosures (PIT XBRL, July 2026 on): --prefetch to the lake, then load."""
    from .ingest import insider_xbrl as IX
    cfg = load()
    start, end = _d(args.start), _d(args.end or args.start)
    if args.prefetch:
        print(IX.prefetch(cfg, start, end))
        return 0
    with Database(cfg).connect() as con:
        print(IX.load(con, cfg, start, end))
    return 0


def cmd_integrated_filing(args) -> int:
    """NSE Integrated Filing - Financials (results from the March-2025 quarter on): --prefetch to the lake, then load."""
    from .ingest import integrated_filing as IF
    cfg = load()
    start, end = _d(args.start), _d(args.end or args.start)
    if args.prefetch:
        syms = None
        if args.universe:
            with Database(cfg, read_only=True).connect() as con:
                syms = {r[0] for r in con.execute("""WITH r AS (SELECT p.ticker, ROW_NUMBER() OVER (PARTITION BY f.business_date
                           ORDER BY f.adv20 DESC) rk FROM features f JOIN adjusted_prices p USING (lineage, business_date)
                           WHERE f.business_date IN (SELECT MAX(business_date) FROM features GROUP BY year(business_date),
                           quarter(business_date)) AND f.business_date >= '2025-01-01' AND f.adv20 > 0)
                           SELECT DISTINCT ticker FROM r WHERE rk <= ?""", [args.universe]).fetchall()}
        print(IF.prefetch(cfg, start, end, symbols=syms, on_progress=lambda a, o: print(a, o, flush=True)))
        return 0
    with Database(cfg).connect() as con:
        print(IF.load(con, cfg, start, end))
    return 0


def cmd_newsref(args) -> int:
    """News referenced by a day's filings - extracted from the filing text, not fetched."""
    from .events import newsref
    with Database(load()).connect() as con:
        print(newsref.extract_day(con, _d(args.date), refresh=args.refresh))
        for dom, head, how in con.execute("""SELECT n.domain, n.headline, n.how
                FROM announcement_news n JOIN announcements a USING (news_id)
                WHERE a.business_date = ? AND n.headline IS NOT NULL
                ORDER BY a.published_at LIMIT 15""", [_d(args.date)]).fetchall():
            print(f"  {(dom or '-'):<28} {how:<9} {head[:95]}")
    return 0


def cmd_tone(args) -> int:
    """Shareholder tone of a day's high-materiality announcements via the model router."""
    from .events import tone
    with Database(load()).connect() as con:
        print(tone.classify_day(con, _d(args.date), limit=args.limit,
                                refresh=args.refresh))
        for r in con.execute("""SELECT t.tone, t.model, ROUND(t.confidence, 3), t.accepted,
                a.company, LEFT(a.headline, 90) FROM announcement_tone t
                JOIN announcements a USING (news_id) WHERE a.business_date = ?
                AND t.tone <> 'neutral' ORDER BY t.accepted DESC, t.tone""",
                             [_d(args.date)]).fetchall():
            print(f"{r[0]:<8} {'ok ' if r[3] else '?? '} {r[1]:<14} {r[2]}  {r[4]}: {r[5]}")
    return 0


def cmd_committee(args) -> int:
    """Convene the investment committee on one company (local models; drafts only)."""
    from .committee import run as committee
    from .worldstate import build as ws
    with Database(load()).connect() as con:
        state = ws.latest(con, _d(args.date) if args.date else None)
        if not state:
            print("no world state yet - run `fb brief` first")
            return 1
        r = committee.convene(con, args.isin, state["version_id"], model=args.model)
    print(f"{args.isin} on {state['business_date']} ({state['version_id']}), model {r.model}")
    for role, s in r.stances.items():
        print(f"  {role:<11} {s['stance']:<8} {s['confidence']}")
    for side, pts in (("BULL", r.bull), ("BEAR", r.bear)):
        for p in pts:
            print(f"  {side} {p['claim']}  {' '.join('[' + i + ']' for i in p['fact_ids'])}")
    print(f"  dropped (uncited) points: {r.dropped_points}")
    print(f"  chair: {r.action}" + (f"  -> draft {r.decision_id}" if r.decision_id else
                                     "  (no draft: a side had no cited point)"))
    return 0


def cmd_regime(args) -> int:
    """Build (or show) the market regime for every session."""
    from .regime import brain
    with Database(load()).connect() as con:
        if args.build:
            print(brain.build(con))
        rows = con.execute(
            """SELECT business_date, regime, raw_regime, ROUND(vix, 1) vix,
                      ROUND(breadth_200 * 100) breadth_pct, ROUND(drawdown * 100, 1) dd_pct,
                      reasons FROM market_regime WHERE version = ?
               ORDER BY business_date DESC LIMIT ?""", [brain.VERSION, args.limit]).fetchall()
    for r in rows:
        print(f"{r[0]}  {r[1]:<9} vix {r[3]}  breadth {r[4]}%  dd {r[5]}%  | {r[6]}")
    return 0


def cmd_daily(args) -> int:
    """The whole daily cycle, idempotent and safe to schedule.

    Each step reports and the next still runs: a missing corporate-action month should
    not cost the morning brief. Exit code is non-zero if any step failed.
    """
    import traceback
    from .ingest import corpact_feed
    from .ingest.announcements import AnnouncementsJob
    from .regime import brain
    cfg = load()
    Database(cfg).migrate()
    d = _d(args.date) if args.date else date.today()
    failed: list[str] = []

    def step(name, fn):
        print(f"== {name}")
        try:
            out = fn()
            if out is not None:
                print(f"   {out}")
        except Exception as e:  # noqa: BLE001 - report and continue
            failed.append(name)
            print(f"   FAILED: {type(e).__name__}: {e}")
            if args.verbose:
                traceback.print_exc()

    def prices():
        res = [r for s in ("NSE", "BSE") for r in BhavcopyIngestJob(s).run_range(d, d)]
        return "; ".join(f"{r.source} {r.status} {r.rows_out}" for r in res)

    def indices():
        return "; ".join(f"{r.status} {r.message}" for r in IndexCloseJob().run_range(d, d))

    def corporate_actions():
        with Database(cfg).connect() as con:
            st = corpact_feed.ingest(con, cfg, d.replace(day=1), d)
            ca_detect.rebuild_derived_factors(con)
        return f"{st['recorded']} new reported actions"

    def announcements():
        res = AnnouncementsJob(cfg).run_range(d - timedelta(days=1), d, force=True)
        return "; ".join(f"{r.business_date} {r.status} {r.message}" for r in res)

    def derive():
        with Database(cfg).connect() as con:
            ca_detect.derive_all(con)
            return ca_detect.auto_triage_gaps(con)

    def regime():
        with Database(cfg).connect() as con:
            return brain.build(con)

    def features_graph():
        from .features import indicators
        from .graph import build as graph
        with Database(cfg).connect() as con:
            f = indicators.build(con)
            g = graph.build(con)
            return {"feature_rows": f["feature_rows"], "groups": g["groups"]}

    def postmortems():
        from .decisions import postmortem
        with Database(cfg).connect() as con:
            return postmortem.record_all(con)
    step("postmortem closed trades", postmortems)

    def promote_drafts():
        from .decisions import promote
        with Database(cfg).connect() as con:
            return promote.run(con)
    step("promote drafts toward paper", promote_drafts)

    def paper_mark():
        from .paper import ledger as paper
        with Database(cfg).connect() as con:
            return paper.mark(con)

    step("prices (NSE + BSE bhavcopy)", prices)
    step("index levels", indices)
    step("BSE corporate actions (this month)", corporate_actions)
    step("announcements (yesterday and today)", announcements)
    if args.full:
        step("derive corporate actions + triage gaps", derive)
    step("market regime", regime)
    step("features + promoter graph", features_graph)

    def mutual_fund_navs():
        from .ingest import mfnav
        with Database(cfg).connect() as con:
            return mfnav.ingest(con, cfg)
    step("mutual-fund NAVs (AMFI)", mutual_fund_navs)

    def news_references():
        from .events import newsref
        with Database(cfg).connect() as con:
            d = con.execute("SELECT MAX(business_date) FROM universe_snapshots").fetchone()[0]
            return newsref.extract_day(con, d)
    step("news references in filings (rules)", news_references)

    def fetch_linked_articles():
        # Only filings whose headline the rules could not recover, and only a handful a
        # day: this is the polite fallback, not a crawl.
        from .ingest import newsfetch
        with Database(cfg).connect() as con:
            day = con.execute("SELECT MAX(business_date) FROM universe_snapshots").fetchone()[0]
            return newsfetch.fetch_day(con, cfg, day, limit=10, only_missing_headline=True)
    step("fetch linked articles (allowlist, robots-aware)", fetch_linked_articles)

    def read_filing_documents():
        from .ingest import filings
        with Database(cfg).connect() as con:
            day = con.execute("SELECT MAX(business_date) FROM universe_snapshots").fetchone()[0]
            return filings.read_day(con, cfg, day, limit=25)
    step("read filing documents (C11)", read_filing_documents)

    def as_reported_financials():
        from .ingest import results as ing
        with Database(cfg).connect() as con:
            day = con.execute("SELECT MAX(business_date) FROM universe_snapshots").fetchone()[0]
            return ing.read_day(con, cfg, day, limit=40)
    step("as-reported financials (C03)", as_reported_financials)

    def announcement_tone():
        from .events import tone
        with Database(cfg).connect() as con:
            d = con.execute("SELECT MAX(business_date) FROM universe_snapshots").fetchone()[0]
            return tone.classify_day(con, d, limit=tone.DAILY_LIMIT, deadline_s=tone.DAILY_DEADLINE_S,
                                     check_busy=True)
    step("announcement tone (model router)", announcement_tone)
    def invalidation_monitor():
        from .decisions import monitor
        with Database(cfg).connect() as con:
            return monitor.run(con, d)
    step("invalidation monitor (live decisions)", invalidation_monitor)

    step("paper trades (close those due)", paper_mark)
    if not args.no_brief:
        class _A:  # reuse `fb brief`
            date = None
        step("daily brief", lambda: cmd_brief(_A()))
    print(f"\n{'OK' if not failed else 'FAILED: ' + ', '.join(failed)}")
    return 1 if failed else 0


def cmd_trace(args) -> int:
    """Follow one claim to its bytes, and every derived claim to its inputs."""
    from .evidence import ledger
    with Database(load()).connect() as con:
        def show(eid: str, depth: int = 0) -> None:
            t = ledger.trace(con, eid)
            pad = "  " * depth
            print(f"{pad}{t['evidence_id']}  [{t['kind']}] {t['claim']}")
            print(f"{pad}  source {t['source']} tier {t['source_tier']}, derivation "
                  f"'{t['derivation']}', as of {t['as_of']}, observed {t['observed_at']}")
            if t.get("url"):
                print(f"{pad}  bytes  {t['url']}")
                print(f"{pad}         sha256 {t['sha256']}  lake {t['lake_key']}")
            if t.get("superseded_by"):
                print(f"{pad}  SUPERSEDED by {t['superseded_by']}")
            if depth == 0 and t["used_by"]:
                print(f"{pad}  used by " + ", ".join(f"{u['used_by_kind']} {u['used_by_id']}"
                                                     for u in t["used_by"]))
            for i in t["inputs"]:
                show(i, depth + 1)
        show(args.evidence_id)
    return 0


def cmd_brief(args) -> int:
    """Build the world state for a session and render its cited daily brief."""
    from .brief.render import render
    from .evidence import ledger
    from .worldstate import build as ws
    cfg = load()
    with Database(cfg).connect() as con:
        d = _d(args.date) if args.date else con.execute(
            "SELECT MAX(business_date) FROM universe_snapshots WHERE exchange = 'NSE'"
        ).fetchone()[0]
        state = ws.build(con, d)
        md, cited = render(con, state, watchlist_path=cfg.data_root / "watchlist.txt")
        ledger.use(con, cited, used_by_kind="brief", used_by_id=f"brief_{d}")
    out = cfg.data_root / "briefs" / f"{d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"world state {state['version_id']}; {len(cited)} citations -> {out}")
    return 0


# ------------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
    # Load .env before anything reads a credential. Real environment variables win, so a
    # one-off `KEY=... fb ...` still overrides the file.
    from . import env as _env
    _env.load()

    p = argparse.ArgumentParser(prog="fb", description="Financial-Brain")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("migrate", help="create/update the schema").set_defaults(fn=cmd_migrate)
    sub.add_parser("status", help="what the system currently holds").set_defaults(fn=cmd_status)

    g = sub.add_parser("inverse", help="read-only inverse-policy research")
    g.add_argument("--synthetic")
    g.add_argument("--history")
    g.add_argument("--authorized", action="store_true")
    g.add_argument("--seed", type=int, default=7)
    g.add_argument("--rows", type=int, default=900)
    g.add_argument("--rules-out")
    g.add_argument("--report-out")
    g.set_defaults(fn=cmd_inverse)
    sub.add_parser("inverse-audit", help="read-only coverage audit of decision histories").set_defaults(fn=cmd_inverse_audit)
    g = sub.add_parser("inverse-tool", help="read-only research comparison and diagnostics")
    g.add_argument("args", nargs=argparse.REMAINDER)
    g.set_defaults(fn=cmd_inverse_tool)

    g = sub.add_parser("ingest", help="ingest bhavcopies for a date range")
    g.add_argument("--start", required=True)
    g.add_argument("--end")
    g.add_argument("--source", action="append", choices=["NSE", "BSE"])
    g.add_argument("--force", action="store_true", help="re-ingest dates already done")
    g.add_argument("--from-lake", action="store_true", help="replay from stored bytes, no network")
    g.add_argument("--strict", action="store_true", help="non-zero exit if any run failed")
    g.add_argument("-v", "--verbose", action="store_true")
    g.set_defaults(fn=cmd_ingest)

    g = sub.add_parser("prefetch", help="fill the raw lake in parallel (network only)")
    g.add_argument("--start", required=True)
    g.add_argument("--end")
    g.add_argument("--source", action="append", choices=["NSE", "BSE"])
    g.add_argument("--kind", default="bhavcopy", choices=["bhavcopy", "index", "announcements"])
    g.add_argument("--workers", type=int, default=6)
    g.set_defaults(fn=cmd_prefetch)

    g = sub.add_parser("daily", help="the whole daily cycle: ingest, regime, brief")
    g.add_argument("--date", help="session date (default: today)")
    g.add_argument("--full", action="store_true",
                   help="also re-derive corporate actions and triage gaps (~12 min)")
    g.add_argument("--no-brief", action="store_true")
    g.add_argument("-v", "--verbose", action="store_true")
    g.set_defaults(fn=cmd_daily)

    g = sub.add_parser("trace", help="follow a claim to its bytes and inputs")
    g.add_argument("evidence_id")
    g.set_defaults(fn=cmd_trace)

    g = sub.add_parser("brief", help="build the world state and render the cited daily brief")
    g.add_argument("--date", help="session date (default: latest)")
    g.set_defaults(fn=cmd_brief)

    g = sub.add_parser("features", help="build adjusted prices + features (Phase 2)")
    g.set_defaults(fn=cmd_features)

    g = sub.add_parser("evaluate", help="rank-IC benchmark of features (Phase 2)")
    g.add_argument("--feature", action="append")
    g.add_argument("--validate", action="store_true",
                   help="run the Alpha Validation Firewall (each run counts as a trial)")
    g.add_argument("--classifier", action="store_true",
                   help="announcement classifier precision on the hand-checked samples")
    g.add_argument("--horizon", type=int, default=20)
    g.add_argument("--start")
    g.add_argument("--end")
    g.set_defaults(fn=cmd_evaluate)

    g = sub.add_parser("graph", help="promoter-group knowledge graph (Phase 2)")
    g.add_argument("--build", action="store_true")
    g.add_argument("--isin")
    g.add_argument("--as-of")
    g.add_argument("--limit", type=int, default=20)
    g.set_defaults(fn=cmd_graph)

    g = sub.add_parser("hypothesis", help="pre-registered hypotheses (register/test/list)")
    g.add_argument("action", choices=["register", "test", "list"])
    g.add_argument("target", nargs="?", help="spec .toml to register, or hypothesis id")
    g.add_argument("--oos", action="store_true", help="out-of-sample test")
    g.set_defaults(fn=cmd_hypothesis)

    g = sub.add_parser("paper", help="paper trades for PAPER_CANDIDATE decisions")
    g.add_argument("action", choices=["open", "mark", "list"])
    g.add_argument("decision_id", nargs="?")
    g.set_defaults(fn=cmd_paper)

    g = sub.add_parser("models", help="tiered models: list, bench, plan (ADR-0002)")
    g.add_argument("action", choices=["list", "bench", "rescore", "route", "plan"])
    g.add_argument("--budget-ms", type=float, default=4000)
    g.add_argument("--verify-on", help="labelled set the route must hold up on "
                   "(e.g. sentiment_holdout)")
    g.add_argument("--max-accepted-error", type=float, default=0.10)
    g.add_argument("--task", default="sentiment",
                   choices=["sentiment", "event_type", "sentiment_holdout",
                            "sentiment_news", "sentiment_news_holdout"])
    g.add_argument("--model", action="append", default=[])
    g.add_argument("--target", type=float, default=0.9)
    g.add_argument("--limit", type=int)
    g.set_defaults(fn=cmd_models)

    g = sub.add_parser("calls", help="earnings-call transcripts and presentations (C11)")
    g.add_argument("--date", required=True)
    g.add_argument("--limit", type=int, default=10)
    g.add_argument("--no-embed", action="store_true")
    g.set_defaults(fn=cmd_calls)

    g = sub.add_parser("vault", help="export the Obsidian vault as a link graph")
    g.add_argument("--root", default="brain")
    g.add_argument("--out")
    g.set_defaults(fn=cmd_vault)

    g = sub.add_parser("timing", help="System One timing model (fit / show)")
    g.add_argument("action", choices=["fit", "show"])
    g.add_argument("--isin")
    g.add_argument("--date")
    g.set_defaults(fn=cmd_timing)

    g = sub.add_parser("quality", help="decision quality, scored apart from P&L")
    g.add_argument("--decision")
    g.set_defaults(fn=cmd_quality)

    g = sub.add_parser("analogues", help="what followed this kind of event before")
    g.add_argument("event_type", nargs="?")
    g.add_argument("--date")
    g.add_argument("--horizon", type=int, default=90)
    g.add_argument("--warm", action="store_true",
                   help="build the cache for every event type with enough filings")
    g.set_defaults(fn=cmd_analogues)

    g = sub.add_parser("reclassify",
                       help="re-apply event rules to the stored archive")
    g.add_argument("--apply", action="store_true",
                   help="write the changes (default is a dry run)")
    g.set_defaults(fn=cmd_reclassify)

    g = sub.add_parser("strategy", help="stateful entry/exit strategies")
    g.add_argument("strategy", nargs="*")
    g.add_argument("--list", action="store_true", help="what each one claims, and its source")
    g.add_argument("--start", default="2016-01-01")
    g.add_argument("--end", default=None)
    g.add_argument("--dry-run", action="store_true",
                   help="do not record the run as a trial")
    g.set_defaults(fn=cmd_strategy)

    g = sub.add_parser("band", help="h18: a no-trade band as wide as the name's own cost")
    g.add_argument("feature", nargs="+")
    g.add_argument("--horizon", type=int, default=20)
    g.add_argument("--positions", type=int, default=40)
    g.add_argument("--direction", type=int, choices=[1, -1], default=1,
                   help="-1 states the hypothesis 'low is good', e.g. low volatility")
    g.add_argument("--segment", default="delivery",
                   choices=["delivery", "intraday", "futures", "options"])
    g.add_argument("--start", default=None)
    g.add_argument("--end", default=None)
    g.set_defaults(fn=cmd_band)

    g = sub.add_parser("kite", help="Kite Connect: daily login and read-only market data")
    g.add_argument("action", choices=["login", "status", "quote", "candles"])
    g.add_argument("--symbol", action="append", help="e.g. NSE:RELIANCE (repeatable)")
    g.add_argument("--kind", default="ohlc", choices=["ltp", "ohlc", "full"])
    g.add_argument("--interval", default="minute")
    g.add_argument("--start")
    g.add_argument("--date")
    g.add_argument("--limit", type=int, default=10)
    g.add_argument("--port", type=int, default=8765)
    g.set_defaults(fn=cmd_kite)

    g = sub.add_parser("research", help="the published-anomaly catalogue")
    g.add_argument("--family")
    g.add_argument("--ready", action="store_true", help="only what is computable now")
    g.add_argument("--blocked", action="store_true", help="what missing data costs us")
    g.add_argument("--verbose", "-v", action="store_true")
    g.set_defaults(fn=cmd_research)

    g = sub.add_parser("safety", help="situational awareness: when not to act")
    g.add_argument("--isin")
    g.add_argument("--date")
    g.add_argument("--position", type=float, default=300_000.0)
    g.set_defaults(fn=cmd_safety)

    g = sub.add_parser("control", help="does the committee beat its own universe?")
    g.set_defaults(fn=cmd_control)

    g = sub.add_parser("lessons", help="what the closed trades actually support (C25)")
    g.set_defaults(fn=cmd_lessons)

    g = sub.add_parser("replay", help="replay the committee over past sessions (C23)")
    g.add_argument("dates", nargs="+", help="past session dates, e.g. 2026-03-16")
    g.add_argument("--per-day", type=int, default=2)
    g.set_defaults(fn=cmd_replay)

    g = sub.add_parser("promote", help="walk drafts toward paper (never to approval)")
    g.set_defaults(fn=cmd_promote)

    g = sub.add_parser("scorecard", help="how this system's own calls have done")
    g.add_argument("--since")
    g.set_defaults(fn=cmd_scorecard)

    g = sub.add_parser("results", help="as-reported financials from results PDFs (C03)")
    g.add_argument("--date", required=True)
    g.add_argument("--limit", type=int, default=10)
    g.set_defaults(fn=cmd_results)

    g = sub.add_parser("results-bulk", help="parse results PDFs for the top-N names, resumably")
    g.add_argument("--start", default="2016-01-01")
    g.add_argument("--end", default="2026-09-30")
    g.add_argument("--names", type=int, default=500)
    g.add_argument("--limit", type=int)
    g.set_defaults(fn=cmd_results_bulk)

    g = sub.add_parser("paper-daily", help="the daily paper loop (decide, remember, resolve; no orders)")
    g.add_argument("--date")
    g.set_defaults(fn=cmd_paper_daily)

    g = sub.add_parser("plan", help="position size, horizon and exits for one name (information, not advice)")
    g.add_argument("ticker")
    g.add_argument("--capital", type=float, default=500_000.0)
    g.add_argument("--style", choices=("position", "swing", "intraday"), default="position")
    g.add_argument("--risk", type=float, default=None, help="fraction of the account lost if the stop is hit (default by style)")
    g.add_argument("--side", choices=("long", "short"), default="long")
    g.set_defaults(fn=cmd_plan)

    g = sub.add_parser("costs-calc", help="itemised cost of a trade, impact range, break-even and tax on a gain")
    g.add_argument("--order", type=float, required=True, help="order value in rupees")
    g.add_argument("--segment", choices=("delivery", "intraday"), default="delivery")
    g.add_argument("--ticker", help="look up turnover and volatility for the impact estimate")
    g.add_argument("--hold", type=int, default=20, help="sessions held (delivery)")
    g.add_argument("--gain", type=float, help="hypothetical gross return, e.g. 0.05 for +5%")
    g.add_argument("--slab", type=float, default=0.30, help="your marginal slab rate, for intraday profit")
    g.add_argument("--table", action="store_true", help="also show cost by order size")
    g.set_defaults(fn=cmd_costs_calc)

    g = sub.add_parser("paper-intraday", help="intraday paper trading: live, --replay DATE, or --report (no orders)")
    g.add_argument("--replay")
    g.add_argument("--report", action="store_true")
    g.add_argument("--names", type=int, default=200)
    g.set_defaults(fn=cmd_paper_intraday)

    g = sub.add_parser("champion", help="champion/challenger on locked out-of-sample data")
    g.add_argument("--record", action="store_true")
    g.set_defaults(fn=cmd_champion)

    g = sub.add_parser("fno-snapshot", help="evening F&O snapshot: basis, OI, IV, positioning (read-only)")
    g.add_argument("--chains", type=int, default=100)
    g.set_defaults(fn=cmd_fno_snapshot)

    g = sub.add_parser("terminal", help="write the trader's terminal page (HTML)")
    g.set_defaults(fn=cmd_terminal)

    g = sub.add_parser("book", help="the suggestion paper book: top picks bought and sold on paper (no orders)")
    g.add_argument("--book", default="all", choices=["all", "book-v1", "swing-v1"], help="which book (default all)")
    g.add_argument("--replay", metavar="START", help="replay the same rules from START (in-sample; never live)")
    g.add_argument("--end", metavar="END", help="last signal day of a replay (default: latest session)")
    g.add_argument("--label", default="replay", help="mode label for a replay, e.g. replay-week")
    g.add_argument("--report", action="store_true", help="print the record only")
    g.set_defaults(fn=cmd_book)

    g = sub.add_parser("loss-review", help="which situations keep losing in the intraday paper record")
    g.add_argument("--mode", choices=["live", "replay"], help="only live or only replayed trades")
    g.add_argument("--propose", action="store_true", help="write losing patterns as PROPOSED hypotheses")
    g.set_defaults(fn=cmd_loss_review)

    g = sub.add_parser("preregs", help="audit pre-registrations against git (before the result, unchanged after)")
    g.set_defaults(fn=cmd_preregs)

    g = sub.add_parser("data-contracts", help="every source's contract and whether it is met today")
    g.set_defaults(fn=cmd_data_contracts)

    g = sub.add_parser("reproduce", help="recreate a recorded trial: input fingerprint + its script")
    g.add_argument("name", nargs="?")
    g.add_argument("--list", action="store_true")
    g.add_argument("--fingerprint-only", action="store_true")
    g.set_defaults(fn=cmd_reproduce)

    g = sub.add_parser("stress", help="stress your holdings: 2016/2018/2020/2022/2024 episodes, market, sector, liquidity")
    g.set_defaults(fn=cmd_stress)

    g = sub.add_parser("my-portfolio", help="your Kite holdings (read only): thesis health, risk, alternatives")
    g.add_argument("--sync", action="store_true", help="read holdings, positions and today's trades from Kite first")
    g.set_defaults(fn=cmd_my_portfolio)

    g = sub.add_parser("journal", help="your round trips from the Kite trade book, with an autopsy each")
    g.add_argument("--import", dest="import_path", help="add fills from a Zerodha Console tradebook export (CSV)")
    g.set_defaults(fn=cmd_journal)

    g = sub.add_parser("alerts", help="desktop alerts: opportunities, holdings exiting by rule, a failed run")
    g.add_argument("--dry-run", action="store_true", help="show what would be sent, send nothing")
    g.set_defaults(fn=cmd_alerts)

    g = sub.add_parser("lifecycle", help="one opportunity end to end: found, taken or rejected, sold, resolved")
    g.add_argument("ticker")
    g.add_argument("--day", required=True, help="the signal day, YYYY-MM-DD")
    g.set_defaults(fn=cmd_lifecycle)

    g = sub.add_parser("daily-results", help="the day's paper results in one report")
    g.add_argument("--day", help="YYYY-MM-DD (default today)")
    g.set_defaults(fn=cmd_daily_results)

    g = sub.add_parser("zoo", help="the live paper zoo: every tested daily strategy on paper")
    g.add_argument("--report", action="store_true", help="print the record only")
    g.set_defaults(fn=cmd_zoo)

    g = sub.add_parser("filings", help="read the PDF a filing points at (C11)")
    g.add_argument("--date", required=True)
    g.add_argument("--limit", type=int, default=15)
    g.set_defaults(fn=cmd_filings)

    g = sub.add_parser("security", help="untrusted-text findings at the model boundary")
    g.add_argument("--limit", type=int, default=20)
    g.set_defaults(fn=cmd_security)

    g = sub.add_parser("monitor", help="re-check live decisions' invalidation conditions")
    g.add_argument("--date")
    g.add_argument("--dry-run", action="store_true", help="show every check, record nothing")
    g.set_defaults(fn=cmd_monitor)

    g = sub.add_parser("fundamentals", help="company ratios from Screener (Tier 3)")
    g.add_argument("symbol", nargs="*")
    g.add_argument("--watchlist", action="store_true",
                   help="every ticker in data/watchlist.txt")
    g.set_defaults(fn=cmd_fundamentals)

    g = sub.add_parser("newsfetch", help="fetch a filing's linked article (robots-aware)")
    g.add_argument("--date", required=True)
    g.add_argument("--limit", type=int, default=20)
    g.add_argument("--only-missing", action="store_true",
                   help="only filings whose headline could not be extracted")
    g.set_defaults(fn=cmd_newsfetch)

    g = sub.add_parser("integrated-filing", help="NSE Integrated Filing results (2025-): --prefetch, then load")
    g.add_argument("--start", required=True)
    g.add_argument("--end")
    g.add_argument("--prefetch", action="store_true")
    g.add_argument("--universe", type=int, default=0, help="limit XBRL downloads to the N most liquid names")
    g.set_defaults(fn=cmd_integrated_filing)

    g = sub.add_parser("nse-archives", help="NSE F&O bhavcopy, ban list, participant OI/volume: --prefetch, then load")
    g.add_argument("--start", required=True)
    g.add_argument("--end")
    g.add_argument("--kinds", help="comma list of fo_bhav,fo_ban,participant_oi,participant_vol (default all)")
    g.add_argument("--prefetch", action="store_true")
    g.set_defaults(fn=cmd_nse_archives)

    g = sub.add_parser("shareholding", help="NSE shareholding patterns (2021Q3 on): --prefetch [--all], then load")
    g.add_argument("--prefetch", action="store_true")
    g.add_argument("--all", action="store_true", help="every traded symbol's full history (backfill)")
    g.set_defaults(fn=cmd_shareholding)

    g = sub.add_parser("bulk-deals", help="NSE bulk/block deals and short selling by month: --prefetch, then load")
    g.add_argument("--start", required=True)
    g.add_argument("--end")
    g.add_argument("--prefetch", action="store_true")
    g.set_defaults(fn=cmd_bulk_deals)

    g = sub.add_parser("index-tri", help="NSE index total-return series and constituent snapshots")
    g.add_argument("--start", default="2015-01-01")
    g.add_argument("--end")
    g.set_defaults(fn=cmd_index_tri)

    g = sub.add_parser("disclosures", help="NSE corporate actions, SAST Reg 29, pledges, encumbrance, event calendar")
    g.add_argument("--start", default="2015-01-01")
    g.add_argument("--end")
    g.add_argument("--datasets", help="comma list (default all)")
    g.add_argument("--since", help="load only files from this month on (nightly)")
    g.add_argument("--prefetch", action="store_true")
    g.set_defaults(fn=cmd_disclosures)

    g = sub.add_parser("kite-history", help="widen Kite pulls: minute bars (top N a year), continuous futures + OI")
    g.add_argument("what", choices=["minute", "futures", "all"])
    g.add_argument("--top", type=int, default=1000)
    g.add_argument("--start", help="minute history start date (YYYY-MM-DD); use for a bounded incremental refresh")
    g.set_defaults(fn=cmd_kite_history)

    g = sub.add_parser("depth-archive", help="market hours: 5-level book + OI for all F&O names each minute")
    g.add_argument("--until", default="15:31")
    g.set_defaults(fn=cmd_depth_archive)

    g = sub.add_parser("rating-actions", help="rebuild credit-rating actions with their direction")
    g.set_defaults(fn=cmd_rating_actions)

    g = sub.add_parser("rating-docs", help="rating direction from the attached letter where the headline gave none")
    g.add_argument("--limit", type=int, default=500)
    g.set_defaults(fn=cmd_rating_docs)

    g = sub.add_parser("monthly-updates", help="index monthly business updates among the announcements")
    g.set_defaults(fn=cmd_monthly_updates)

    g = sub.add_parser("calls-backfill", help="earnings-call transcripts and presentations over a date range")
    g.add_argument("--start", default="2021-01-01")
    g.add_argument("--end")
    g.add_argument("--per-day", type=int, default=200)
    g.set_defaults(fn=cmd_calls_backfill)

    g = sub.add_parser("flows", help="today's FII/DII cash-market flows (a forward record)")
    g.set_defaults(fn=cmd_flows)

    g = sub.add_parser("macro", help="FRED macro series and MoSPI CPI/IIP")
    g.add_argument("--mospi-from", type=int, help="first year of MoSPI to fetch (default: last year; 2013 to backfill)")
    g.set_defaults(fn=cmd_macro)

    g = sub.add_parser("insider", help="NSE structured insider disclosures (2026-07 on): --prefetch, then load")
    g.add_argument("--start", required=True)
    g.add_argument("--end")
    g.add_argument("--prefetch", action="store_true")
    g.set_defaults(fn=cmd_insider)

    g = sub.add_parser("delivery", help="NSE delivery position (MTO): --prefetch to the lake, then load")
    g.add_argument("--start", required=True)
    g.add_argument("--end")
    g.add_argument("--prefetch", action="store_true")
    g.add_argument("--workers", type=int, default=4)
    g.set_defaults(fn=cmd_delivery)

    g = sub.add_parser("mfnav", help="mutual-fund NAVs from AMFI (public feed)")
    g.add_argument("--date")
    g.set_defaults(fn=cmd_mfnav)

    g = sub.add_parser("newsref", help="news a filing refers to, from the filing's own text")
    g.add_argument("--date", required=True)
    g.add_argument("--refresh", action="store_true")
    g.set_defaults(fn=cmd_newsref)

    g = sub.add_parser("tone", help="shareholder tone of announcements (model router)")
    g.add_argument("--refresh", action="store_true",
                   help="also re-classify filings left by a superseded route")
    g.add_argument("--date", required=True)
    g.add_argument("--limit", type=int)
    g.set_defaults(fn=cmd_tone)

    g = sub.add_parser("committee", help="investment committee on one ISIN (drafts only)")
    g.add_argument("--isin", required=True)
    g.add_argument("--date")
    g.add_argument("--model", default="llama3.1:8b")
    g.set_defaults(fn=cmd_committee)

    g = sub.add_parser("regime", help="market regime per session (build with --build)")
    g.add_argument("--build", action="store_true")
    g.add_argument("--limit", type=int, default=10)
    g.set_defaults(fn=cmd_regime)

    g = sub.add_parser("announcements", help="ingest + classify BSE announcements")
    g.add_argument("--start", required=True)
    g.add_argument("--end")
    g.add_argument("--force", action="store_true")
    g.add_argument("-v", "--verbose", action="store_true")
    g.set_defaults(fn=cmd_announcements)

    g = sub.add_parser("corpact-feed", help="ingest BSE corporate actions (Tier 1)")
    g.add_argument("--start", required=True, help="YYYY-MM")
    g.add_argument("--end", help="YYYY-MM (default: this month)")
    g.set_defaults(fn=cmd_corpact_feed)

    g = sub.add_parser("corpact-reconcile", help="measure derived vs reported actions")
    g.set_defaults(fn=cmd_corpact_reconcile)

    g = sub.add_parser("kite-index-fill",
                       help="fill index days NSE lacks, from Kite (needs KITE_API_KEY/KITE_ACCESS_TOKEN)")
    g.add_argument("--index", action="append", help="limit to these index names")
    g.set_defaults(fn=cmd_kite_index_fill)

    g = sub.add_parser("lake-register", help="register every lake object in lake_manifest")
    g.set_defaults(fn=cmd_lake_register)

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

    sub.add_parser("reference", help="sync exchange reference data").set_defaults(fn=cmd_reference)

    g = sub.add_parser("index", help="ingest NSE index closes (benchmark history)")
    g.add_argument("--start", required=True)
    g.add_argument("--end")
    g.add_argument("--force", action="store_true")
    g.add_argument("-v", "--verbose", action="store_true")
    g.set_defaults(fn=cmd_index)

    g = sub.add_parser("derive", help="derive corporate actions from restated prev closes")
    g.add_argument("--dry-run", action="store_true", help="show candidates, write nothing")
    g.add_argument("--include-single-exchange", action="store_true",
                   help="also record candidates only one exchange restated (noisy - "
                        "mostly illiquid BSE names, not real actions)")
    g.add_argument("--min-deviation", type=float, default=0.02)
    g.add_argument("--limit", type=int, default=30)
    g.add_argument("--rebuild", action="store_true",
                   help="clear all derived actions first (use after a detector fix)")
    g.set_defaults(fn=cmd_derive)

    g = sub.add_parser("gaps", help="triage unexplained overnight price gaps")
    g.add_argument("--review", metavar="ISIN:DATE:VERDICT[:NOTE]")
    g.add_argument("--auto-review", action="store_true",
                   help="apply the documented triage rule to every untriaged gap")
    g.add_argument("--redo", action="store_true",
                   help="with --auto-review: replace earlier automatic verdicts "
                        "(manual ones are never touched)")
    g.add_argument("--threshold", type=float, default=0.35)
    g.add_argument("--limit", type=int, default=30)
    g.set_defaults(fn=cmd_gaps)

    g = sub.add_parser("gate", help="Phase 0 completion gate - can Phase 1 start?")
    g.add_argument("--min-days", type=int, default=60)
    g.set_defaults(fn=cmd_gate)

    g = sub.add_parser("benchmark", help="index history coverage")
    g.add_argument("--limit", type=int, default=25)
    g.set_defaults(fn=cmd_benchmark)

    # Subcommands that are pure formatters over ``services`` register themselves from
    # ``commands/``. The operational commands above - ingest, prefetch, daily, dq, gaps - stay
    # here: they move bytes and report what moved, so there is no domain question to route
    # through a service and inventing one would be churn with no reader served.
    from . import commands as _commands
    _commands.register_all(sub)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
