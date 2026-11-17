"""Persist the instrument master and calendars to DuckDB (tables in ``storage/schema.sql``).

Rows are append-only facts with their provenance. Loading replays them through ``InstrumentMaster``, so
a stored set that violates an invariant fails to load instead of loading wrong.
"""
from __future__ import annotations

from datetime import datetime

from financial_brain.instruments.clocks import Calendar, CalendarKind
from financial_brain.instruments.master import (
    InstrumentId,
    InstrumentMaster,
    Provenance,
    StateChange,
    Succession,
    Terms,
)


def _prov(p: Provenance) -> list:
    return [p.source, p.source_hash, p.published_at, p.ingested_at]


def _dec(v) -> str | None:
    return None if v is None else str(v)


def save(con, master: InstrumentMaster) -> dict[str, int]:
    """Replace the stored master with this one, in a single transaction."""
    r = master.records()
    con.execute("BEGIN")
    try:
        for t in ("instrument_identity", "instrument_terms", "instrument_state", "instrument_succession"):
            con.execute(f"DELETE FROM {t}")
        for v in r["identity"]:
            con.execute("INSERT INTO instrument_identity VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        [v.uid, v.asset_class.value, v.market, v.exchange, v.symbol, v.isin,
                         v.provider_token, v.effective_from, v.effective_to, v.underlying,
                         *_prov(v.provenance)])
        for t in r["terms"]:
            con.execute("INSERT INTO instrument_terms VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        [t.uid, t.effective_from, _dec(t.tick), t.lot, _dec(t.multiplier), t.expiry,
                         _dec(t.strike), t.option_type, t.benchmark, t.mf_plan, t.mf_option,
                         t.unit_places, *_prov(t.provenance)])
        for s in r["state"]:
            con.execute("INSERT INTO instrument_state VALUES (?,?,?,?,?,?,?)",
                        [s.uid, s.effective_from, s.state.value, *_prov(s.provenance)])
        for s in r["succession"]:
            con.execute("INSERT INTO instrument_succession VALUES (?,?,?,?,?,?,?,?,?)",
                        [s.old_uid, s.new_uid, s.effective_date, s.kind, str(s.ratio),
                         *_prov(s.provenance)])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {k: len(v) for k, v in r.items()}


def load(con, *, now: datetime) -> InstrumentMaster:
    m = InstrumentMaster()
    for row in con.execute("SELECT * FROM instrument_identity ORDER BY effective_from, uid").fetchall():
        m.add_identity(InstrumentId(uid=row[0], asset_class=row[1], market=row[2], exchange=row[3],
                                    symbol=row[4], isin=row[5], provider_token=row[6],
                                    effective_from=row[7], effective_to=row[8], underlying=row[9],
                                    provenance=Provenance(*row[10:14])), now=now)
    for row in con.execute("SELECT * FROM instrument_terms ORDER BY effective_from").fetchall():
        m.add_terms(Terms(uid=row[0], effective_from=row[1], tick=row[2], lot=row[3], multiplier=row[4],
                          expiry=row[5], strike=row[6], option_type=row[7], benchmark=row[8],
                          mf_plan=row[9], mf_option=row[10], unit_places=row[11],
                          provenance=Provenance(*row[12:16])), now=now)
    for row in con.execute("SELECT * FROM instrument_state ORDER BY effective_from").fetchall():
        m.add_state(StateChange(uid=row[0], effective_from=row[1], state=row[2],
                                provenance=Provenance(*row[3:7])), now=now)
    for row in con.execute("SELECT * FROM instrument_succession ORDER BY effective_date").fetchall():
        m.add_succession(Succession(old_uid=row[0], new_uid=row[1], effective_date=row[2], kind=row[3],
                                    ratio=row[4], provenance=Provenance(*row[5:9])), now=now)
    return m


def save_calendar(con, cal: Calendar) -> int:
    """Store a calendar as its coverage row plus one row per exception day."""
    con.execute("BEGIN")
    try:
        con.execute("DELETE FROM trading_calendar WHERE name = ?", [cal.name])
        con.execute("DELETE FROM trading_calendar_day WHERE name = ?", [cal.name])
        con.execute("INSERT INTO trading_calendar VALUES (?,?,?,?,?)",
                    [cal.name, cal.kind.value, cal.covers_from, cal.covers_through, cal.source])
        rows = [(cal.name, d, "HOLIDAY") for d in sorted(cal.holidays)]
        rows += [(cal.name, d, "SPECIAL_SESSION") for d in sorted(cal.special_sessions)]
        if rows:
            con.executemany("INSERT INTO trading_calendar_day VALUES (?,?,?)", rows)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return len(rows)


def load_calendar(con, name: str) -> Calendar:
    head = con.execute("SELECT kind, covers_from, covers_through, source FROM trading_calendar "
                       "WHERE name = ?", [name]).fetchone()
    if head is None:
        raise KeyError(f"no stored calendar named {name!r}")
    days = con.execute("SELECT day, kind FROM trading_calendar_day WHERE name = ?", [name]).fetchall()
    return Calendar(name, CalendarKind(head[0]), head[1], head[2],
                    holidays=frozenset(d for d, k in days if k == "HOLIDAY"), source=head[3],
                    special_sessions=frozenset(d for d, k in days if k == "SPECIAL_SESSION"))
