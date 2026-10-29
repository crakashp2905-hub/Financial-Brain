"""Forward-only attestation for champion picks; historical rows remain unverified."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone

from . import champion

IST = timezone(timedelta(hours=5, minutes=30))
SCHEMA = """CREATE TABLE IF NOT EXISTS champion_pick_attestations (
    day VARCHAR PRIMARY KEY, recorded_at_utc TIMESTAMP, latest_price_day VARCHAR,
    status VARCHAR, strategies INTEGER, picks_sha256 VARCHAR)"""


def _pick_digest(con, day: str) -> str:
    rows = con.execute("""SELECT strategy, ticker, lineage, cost FROM champion_picks
                          WHERE day = ? ORDER BY strategy, ticker, lineage, cost""", [day]).fetchall()
    payload = json.dumps(rows, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record(research_con, ledger_con, day: date, *, now: datetime | None = None) -> dict:
    """Record only a current, pre-outcome session; fail closed on stale data."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise champion.ChampionError("recording clock must include timezone")
    today = now.astimezone(IST).date()
    if day > today or (today - day).days > 4:
        raise champion.ChampionError("pick day must be current (within four calendar days)")
    if day == today and now.astimezone(IST).time() < time(15, 30):
        raise champion.ChampionError("same-day champion picks require the completed cash session")
    latest = research_con.execute("SELECT MAX(business_date) FROM adjusted_prices").fetchone()[0]
    if latest is None or str(latest) != day.isoformat():
        raise champion.ChampionError("price archive must end on the pick day; future outcomes or missing prices")
    ledger_con.execute(SCHEMA)
    ledger_con.execute("ALTER TABLE champion_pick_attestations ADD COLUMN IF NOT EXISTS picks_sha256 VARCHAR")
    previous = ledger_con.execute("SELECT 1 FROM champion_pick_attestations WHERE day = ?", [day.isoformat()]).fetchone()
    if previous:
        raise champion.ChampionError("session already attested; overwrite is forbidden")
    existing = ledger_con.execute("SELECT 1 FROM champion_picks WHERE day = ? LIMIT 1", [day.isoformat()]).fetchone() if _has_picks(ledger_con) else None
    if existing:
        raise champion.ChampionError("unattested picks already exist for this day")
    ledger_con.begin()
    try:
        counts = champion.record(research_con, ledger_con, day)
        digest = _pick_digest(ledger_con, day.isoformat())
        ledger_con.execute("INSERT INTO champion_pick_attestations "
                           "(day, recorded_at_utc, latest_price_day, status, strategies, picks_sha256) "
                           "VALUES (?,?,?,?,?,?)",
                           [day.isoformat(), now.astimezone(timezone.utc).replace(tzinfo=None),
                            str(latest), "FORWARD_ATTESTED", len(counts), digest])
        ledger_con.commit()
    except Exception:
        ledger_con.rollback()
        raise
    return counts


def _has_picks(con) -> bool:
    return bool(con.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'champion_picks'").fetchone())


def audit(ledger_con) -> dict:
    """Classify evidence without mutating the ledger or retroactively attesting rows."""
    tables = {r[0] for r in ledger_con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    if "champion_picks" not in tables:
        return {"status": "NO_PICKS", "attested_days": 0, "unattested_days": 0,
                "resolved_attested_days": 0, "note": "No champion pick ledger."}
    attested = ("champion_pick_attestations" in tables)
    rows = ledger_con.execute("SELECT DISTINCT day FROM champion_picks").fetchall()
    days = {r[0] for r in rows}
    verified = verified_days(ledger_con)
    resolved = {r[0] for r in ledger_con.execute("SELECT DISTINCT day FROM champion_cohorts").fetchall()} if "champion_cohorts" in tables else set()
    return {"status": "ATTESTED_COHORTS_AVAILABLE" if verified & resolved else "NOT_PROSPECTIVELY_VERIFIED",
            "attested_days": len(days & verified), "unattested_days": len(days - verified),
            "resolved_attested_days": len(days & verified & resolved),
            "invalid_attestations": _invalid_count(ledger_con) if attested else 0,
            "note": "Local timestamps and hashes detect inconsistency, not independent proof of when picks were made."}


def verified_days(ledger_con) -> set[str]:
    """Only days whose pick batch was written through the forward gate."""
    tables = {r[0] for r in ledger_con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    if "champion_pick_attestations" not in tables:
        return set()
    cols = {r[0] for r in ledger_con.execute("DESCRIBE champion_pick_attestations").fetchall()}
    if "picks_sha256" not in cols or not _has_picks(ledger_con):
        return set()
    return {day for day, latest, digest in ledger_con.execute(
        "SELECT day, latest_price_day, picks_sha256 FROM champion_pick_attestations "
        "WHERE status = 'FORWARD_ATTESTED'").fetchall()
        if day == latest and digest and digest == _pick_digest(ledger_con, day)}


def _invalid_count(ledger_con) -> int:
    total = ledger_con.execute("SELECT COUNT(*) FROM champion_pick_attestations "
                               "WHERE status = 'FORWARD_ATTESTED'").fetchone()[0]
    return total - len(verified_days(ledger_con))
