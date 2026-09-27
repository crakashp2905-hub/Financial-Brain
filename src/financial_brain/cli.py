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
            return tone.classify_day(con, d, limit=tone.DAILY_LIMIT)
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
