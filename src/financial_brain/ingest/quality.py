"""Data-quality contracts.

A contract is a named assertion over a freshly-parsed payload. Contracts run *before*
anything reaches the curated layer.

Two scopes, and the distinction matters:

``FILE``  the payload as a whole is untrustworthy - wrong date, too few rows, duplicated
          instruments. Nothing from it may be published.
``ROW``   individual rows are impossible - high < low, negative volume, a malformed ISIN.
          The offending rows are rejected and recorded; the rest of the file is published.

Row-level rejection exists because exchanges really do publish bad rows. On 2026-07-29
BSE published `Maple Infrastructure Trust` with open=high=low=144.00 and close=142.50 -
a close outside the day's own range. Discarding 4,929 good rows over one impossible one
would be the wrong trade.

The philosophy either way: it is much cheaper to reject bad data today than to discover
in 2029 that six weeks of 2026 prices were quietly wrong.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

ERROR = "error"
WARN = "warn"

FILE = "file"
ROW = "row"


@dataclass
class CheckResult:
    name: str
    severity: str
    passed: bool
    observed: str = ""
    detail: str = ""
    scope: str = FILE
    checked_at: str = ""

    def __post_init__(self):
        if not self.checked_at:
            self.checked_at = datetime.now(timezone.utc).isoformat()


#: SQL predicates identifying *bad* rows, keyed by check name.
ROW_PREDICATES = {
    "isin_well_formed":
        "ISIN IS NOT NULL AND TRIM(ISIN) <> '' "
        "AND NOT regexp_matches(ISIN, '^[A-Z]{2}[A-Z0-9]{9}[0-9]$')",
    "ohlc_coherent":
        "TRY_CAST(HghPric AS DOUBLE) IS NOT NULL AND TRY_CAST(LwPric AS DOUBLE) IS NOT NULL "
        "AND (TRY_CAST(HghPric AS DOUBLE) < TRY_CAST(LwPric AS DOUBLE) "
        " OR TRY_CAST(ClsPric AS DOUBLE) > TRY_CAST(HghPric AS DOUBLE) "
        " OR TRY_CAST(ClsPric AS DOUBLE) < TRY_CAST(LwPric AS DOUBLE))",
    "close_positive":
        "TRY_CAST(ClsPric AS DOUBLE) IS NOT NULL AND TRY_CAST(ClsPric AS DOUBLE) <= 0",
    "volume_non_negative":
        "TRY_CAST(TtlTradgVol AS BIGINT) < 0",
}


class QualityReport:
    def __init__(self, results: list[CheckResult]):
        self.results = results

    @property
    def failed(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed and r.severity == ERROR]

    @property
    def fatal(self) -> list[CheckResult]:
        """File-scope errors. These stop publication entirely."""
        return [r for r in self.failed if r.scope == FILE]

    @property
    def row_level(self) -> list[CheckResult]:
        """Row-scope errors. These reject rows, not the file."""
        return [r for r in self.failed if r.scope == ROW]

    @property
    def warnings(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed and r.severity == WARN]

    @property
    def publishable(self) -> bool:
        return not self.fatal

    def summary(self) -> str:
        bits = []
        if self.fatal:
            bits.append("FATAL: " + "; ".join(f"{r.name}={r.observed}" for r in self.fatal))
        if self.row_level:
            bits.append("rejected rows: " +
                        "; ".join(f"{r.name}={r.observed}" for r in self.row_level))
        if self.warnings:
            bits.append(f"{len(self.warnings)} warning(s): " +
                        "; ".join(f"{r.name}={r.observed}" for r in self.warnings))
        return " | ".join(bits) if bits else f"{len(self.results)} checks passed"


def check_bhavcopy(con, table: str, *, business_date: date, min_rows: int = 500) -> QualityReport:
    """Contracts for a parsed UDiFF bhavcopy sitting in ``table``."""
    out: list[CheckResult] = []

    def scalar(sql: str):
        return con.execute(sql).fetchone()[0]

    # ---- file scope: is this file the right file, and coherent as a whole? ----
    rows = scalar(f"SELECT COUNT(*) FROM {table}")
    out.append(CheckResult(
        "row_count_plausible", ERROR, rows >= min_rows, str(rows),
        f"expected >= {min_rows} rows for a trading day", FILE))

    wrong_date = scalar(
        f"SELECT COUNT(*) FROM {table} WHERE TRY_CAST(TradDt AS DATE) <> DATE '{business_date}'")
    out.append(CheckResult(
        "trade_date_matches_request", ERROR, wrong_date == 0, str(wrong_date),
        f"rows whose TradDt is not {business_date}", FILE))

    # FinInstrmId is part of instrument identity, not decoration. BSE lists some
    # securities under two scrip codes sharing one ISIN - IDFC traded as both 532659 and
    # 632659 on 2016-12-08, at different closes. Those are two instruments, not a
    # duplicated one, and omitting the id from this key quarantined 194 otherwise-good
    # days.
    dup = scalar(
        f"""SELECT COALESCE(MAX(n), 0) FROM (
              SELECT COUNT(*) AS n FROM {table}
              GROUP BY ISIN, FinInstrmId, TckrSymb, SctySrs, FinInstrmTp,
                       XpryDt, StrkPric, OptnTp)""")
    out.append(CheckResult(
        "no_duplicate_instrument_rows", ERROR, dup <= 1, str(dup),
        "the same instrument appears more than once in one file", FILE))

    equities = scalar(f"SELECT COUNT(*) FROM {table} WHERE FinInstrmTp = 'STK'")
    out.append(CheckResult(
        "has_equity_rows", ERROR, equities > 0, str(equities),
        "file contains no STK instruments at all", FILE))

    # ---- row scope: which individual rows are impossible? ----
    for name, predicate in ROW_PREDICATES.items():
        bad = scalar(f"SELECT COUNT(*) FROM {table} WHERE {predicate}")
        out.append(CheckResult(
            name, ERROR, bad == 0, str(bad),
            f"rows failing {name}; these are rejected, the file still publishes", ROW))

    # ---- advisory ----
    missing_isin = scalar(f"SELECT COUNT(*) FROM {table} WHERE ISIN IS NULL OR TRIM(ISIN) = ''")
    out.append(CheckResult(
        "isin_present", WARN, missing_isin == 0, str(missing_isin),
        "rows without an ISIN (some instrument types legitimately lack one)", FILE))

    return QualityReport(out)


def partition_rows(con, table: str, report: QualityReport, *, clean: str = "raw_clean",
                   rejected: str = "raw_rejected") -> int:
    """Split ``table`` into clean and rejected rows using the failing row-level checks.

    Returns the number of rejected rows. When nothing failed at row scope, ``clean`` is
    simply the whole table.
    """
    failing = [r.name for r in report.row_level]
    if not failing:
        con.execute(f"CREATE OR REPLACE TEMP TABLE {clean} AS SELECT * FROM {table}")
        con.execute(f"CREATE OR REPLACE TEMP TABLE {rejected} AS "
                    f"SELECT *, CAST(NULL AS VARCHAR) AS reject_reason FROM {table} WHERE FALSE")
        return 0

    reason = "CASE " + " ".join(
        f"WHEN {ROW_PREDICATES[n]} THEN '{n}'" for n in failing) + " END"
    bad_any = " OR ".join(f"({ROW_PREDICATES[n]})" for n in failing)

    con.execute(f"CREATE OR REPLACE TEMP TABLE {rejected} AS "
                f"SELECT *, {reason} AS reject_reason FROM {table} WHERE {bad_any}")
    con.execute(f"CREATE OR REPLACE TEMP TABLE {clean} AS "
                f"SELECT * FROM {table} WHERE NOT ({bad_any})")
    return con.execute(f"SELECT COUNT(*) FROM {rejected}").fetchone()[0]
