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
ANCHOR_SCHEMA = """CREATE TABLE IF NOT EXISTS champion_pick_anchors (
    day VARCHAR PRIMARY KEY, provider VARCHAR NOT NULL, anchored_at_utc TIMESTAMP NOT NULL,
    picks_sha256 VARCHAR NOT NULL, receipt VARCHAR NOT NULL)"""


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


def audit(ledger_con, *, verifier=None) -> dict:
    """Classify evidence without mutating the ledger or retroactively attesting rows."""
    tables = {r[0] for r in ledger_con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    if "champion_picks" not in tables:
        return {"status": "NO_PICKS", "attested_days": 0, "unattested_days": 0,
                "resolved_attested_days": 0, "note": "No champion pick ledger."}
    attested = ("champion_pick_attestations" in tables)
    rows = ledger_con.execute("SELECT DISTINCT day FROM champion_picks").fetchall()
    days = {r[0] for r in rows}
    verified = locally_verified_days(ledger_con)
    independent = verified_days(ledger_con, verifier=verifier)
    resolved = {r[0] for r in ledger_con.execute("SELECT DISTINCT day FROM champion_cohorts").fetchall()} if "champion_cohorts" in tables else set()
    return {"status": "LOCAL_COHORTS_AVAILABLE_NOT_INDEPENDENTLY_ATTESTED" if verified & resolved else "NOT_PROSPECTIVELY_VERIFIED",
            "attested_days": len(days & verified), "unattested_days": len(days - verified),
            "resolved_attested_days": len(days & verified & resolved),
            "independently_verified_days": len(days & independent),
            "invalid_attestations": _invalid_count(ledger_con) if attested else 0,
            "note": "Local timestamps and hashes detect inconsistency, not independent proof of when picks were made."}


def locally_verified_days(ledger_con) -> set[str]:
    """Local digest integrity only; this is not a promotion-grade timestamp."""
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


def attach_anchor(ledger_con, day: date, *, provider: str, anchored_at_utc: datetime,
                  receipt: str) -> None:
    """Store an external receipt; verification happens again on every read."""
    if not provider or not receipt or anchored_at_utc.tzinfo is None:
        raise champion.ChampionError("external anchor needs provider, receipt and aware timestamp")
    key = day.isoformat()
    if key not in locally_verified_days(ledger_con):
        raise champion.ChampionError("cannot anchor missing or mutated picks")
    # Conservative next-calendar-day deadline; exchange holidays can only make
    # this earlier than the actual next eligible cash session.
    deadline = datetime.combine(day + timedelta(days=1), time(9, 15), IST)
    if anchored_at_utc >= deadline:
        raise champion.ChampionError("anchor arrived after the earliest possible next entry")
    recorded = ledger_con.execute("SELECT recorded_at_utc FROM champion_pick_attestations WHERE day = ?",
                                  [key]).fetchone()[0].replace(tzinfo=timezone.utc)
    if anchored_at_utc < recorded:
        raise champion.ChampionError("external anchor predates the local pick record")
    ledger_con.execute(ANCHOR_SCHEMA)
    if ledger_con.execute("SELECT 1 FROM champion_pick_anchors WHERE day = ?", [key]).fetchone():
        raise champion.ChampionError("anchor already exists")
    ledger_con.execute("INSERT INTO champion_pick_anchors VALUES (?,?,?,?,?)",
                       [key, provider, anchored_at_utc.astimezone(timezone.utc).replace(tzinfo=None),
                        _pick_digest(ledger_con, key), receipt])


def verified_days(ledger_con, *, verifier=None) -> set[str]:
    """Promotion-grade days need an independently verified signed receipt.

    No trusted provider is configured by default, so the fail-closed result is
    empty. ``verifier(provider, day, digest, anchored_at_utc, receipt)`` must
    validate an external signature and timestamp against a pinned trust root.
    """
    if verifier is None:
        return set()
    tables = {r[0] for r in ledger_con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    if "champion_pick_anchors" not in tables:
        return set()
    local = locally_verified_days(ledger_con)
    out = set()
    for day, provider, anchored, digest, receipt in ledger_con.execute(
            "SELECT day, provider, anchored_at_utc, picks_sha256, receipt FROM champion_pick_anchors").fetchall():
        deadline = datetime.combine(date.fromisoformat(day) + timedelta(days=1), time(9, 15), IST)
        recorded = ledger_con.execute("SELECT recorded_at_utc FROM champion_pick_attestations WHERE day = ?",
                                      [day]).fetchone()
        if (day in local and recorded and recorded[0] <= anchored < deadline.astimezone(timezone.utc).replace(tzinfo=None)
                and digest == _pick_digest(ledger_con, day)):
            try:
                if verifier(provider, day, digest, anchored, receipt):
                    out.add(day)
            except Exception:  # noqa: BLE001 - unavailable verifier never grants promotion
                continue
    return out


def _invalid_count(ledger_con) -> int:
    total = ledger_con.execute("SELECT COUNT(*) FROM champion_pick_attestations "
                               "WHERE status = 'FORWARD_ATTESTED'").fetchone()[0]
    return total - len(locally_verified_days(ledger_con))
