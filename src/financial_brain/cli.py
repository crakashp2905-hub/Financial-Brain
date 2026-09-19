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
    from .evaluation import benchmark
    feats = args.feature or sorted(benchmark.FEATURES)
    with Database(load()).connect() as con:
        for f in feats:
            r = benchmark.evaluate(con, f, args.horizon, start=args.start, end=args.end)
            print(f"{f:<14} h={r['horizon']:<3} dates {r['dates']:<4} IC {r['mean_ic']:+.3f} "
                  f"t {r['ic_t']:+.1f} hit {r['hit_rate']:.0%} Q5-Q1 {r['mean_spread']:+.2%} "
                  f"names {r['avg_names']:.0f} dropped {r['dropped_no_outcome']}")
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

    step("prices (NSE + BSE bhavcopy)", prices)
    step("index levels", indices)
    step("BSE corporate actions (this month)", corporate_actions)
    step("announcements (yesterday and today)", announcements)
    if args.full:
        step("derive corporate actions + triage gaps", derive)
    step("market regime", regime)
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
    g.add_argument("--horizon", type=int, default=20)
    g.add_argument("--start")
    g.add_argument("--end")
    g.set_defaults(fn=cmd_evaluate)

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

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
