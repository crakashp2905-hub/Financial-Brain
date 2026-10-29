"""Forward-only attestation for champion picks; historical rows remain unverified."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from . import champion

IST = timezone(timedelta(hours=5, minutes=30))
SCHEMA = """CREATE TABLE IF NOT EXISTS champion_pick_attestations (
    day VARCHAR PRIMARY KEY, recorded_at_utc TIMESTAMP, latest_price_day VARCHAR,
    status VARCHAR, strategies INTEGER)"""


def record(research_con, ledger_con, day: date, *, now: datetime | None = None) -> dict:
    """Record only a current, pre-outcome session; fail closed on stale data."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise champion.ChampionError("recording clock must include timezone")
    today = now.astimezone(IST).date()
    if day > today or (today - day).days > 4:
        raise champion.ChampionError("pick day must be current (within four calendar days)")
    latest = research_con.execute("SELECT MAX(business_date) FROM adjusted_prices").fetchone()[0]
    if latest is None or str(latest) != day.isoformat():
        raise champion.ChampionError("price archive must end on the pick day; future outcomes or missing prices")
    ledger_con.execute(SCHEMA)
    previous = ledger_con.execute("SELECT 1 FROM champion_pick_attestations WHERE day = ?", [day.isoformat()]).fetchone()
    if previous:
        raise champion.ChampionError("session already attested; overwrite is forbidden")
    existing = ledger_con.execute("SELECT 1 FROM champion_picks WHERE day = ? LIMIT 1", [day.isoformat()]).fetchone() if _has_picks(ledger_con) else None
    if existing:
        raise champion.ChampionError("unattested picks already exist for this day")
    ledger_con.begin()
    try:
        counts = champion.record(research_con, ledger_con, day)
        ledger_con.execute("INSERT INTO champion_pick_attestations VALUES (?,?,?,?,?)",
                           [day.isoformat(), now.astimezone(timezone.utc).replace(tzinfo=None),
                            str(latest), "FORWARD_ATTESTED", len(counts)])
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
    verified = {r[0] for r in ledger_con.execute(
        "SELECT day FROM champion_pick_attestations WHERE status = 'FORWARD_ATTESTED'").fetchall()} if attested else set()
    resolved = {r[0] for r in ledger_con.execute("SELECT DISTINCT day FROM champion_cohorts").fetchall()} if "champion_cohorts" in tables else set()
    return {"status": "ATTESTED_COHORTS_AVAILABLE" if verified & resolved else "NOT_PROSPECTIVELY_VERIFIED",
            "attested_days": len(days & verified), "unattested_days": len(days - verified),
            "resolved_attested_days": len(days & verified & resolved),
            "note": "Old rows without write-time attestation cannot be retroactively treated as forward evidence."}


def verified_days(ledger_con) -> set[str]:
    """Only days whose pick batch was written through the forward gate."""
    tables = {r[0] for r in ledger_con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    if "champion_pick_attestations" not in tables:
        return set()
    return {r[0] for r in ledger_con.execute(
        "SELECT day FROM champion_pick_attestations WHERE status = 'FORWARD_ATTESTED'").fetchall()}
