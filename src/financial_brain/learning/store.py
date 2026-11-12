"""The lessons ledger: what was learned, when, on what evidence, and who decided - append-only.

State is never overwritten. Every step in a lesson's life is a row in ``lesson_events``; the current stage of a lesson is
folded from its events. A pattern that was refuted stays refuted in the record, an owner's approval is a row with their name
and the hash of exactly what they approved, and a later reader can replay how a rule came to be in force. Rows are unique on
``(lesson_id, kind, session)``, so re-running a night changes nothing.

Alongside the events are the derived tables the loop produces and can rebuild: trade autopsies, operational incidents, and a
log of each loop run.

``data/lessons.duckdb`` has one writer (the nightly job, or the owner at a terminal approving a rule). It is in the backup tier
of irreplaceable evidence: the owner's decisions live here.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

PATH = Path("data") / "lessons.duckdb"

#: event kind -> the stage it puts a lesson in. Kinds not listed (DRAFTED, NOTE, DECAYING...) annotate without moving it.
STAGE_OF = {"NOMINATED": "NOMINATED", "CONFIRMED": "CONFIRMED", "REFUTED": "REFUTED", "EXPIRED": "EXPIRED",
            "REGISTERED": "REGISTERED", "SHADOW": "SHADOW", "APPROVED": "APPROVED", "ACTIVATED": "ACTIVE",
            "RETIRED": "RETIRED"}
TERMINAL = ("REFUTED", "EXPIRED", "RETIRED")
KINDS = tuple(STAGE_OF) + ("DRAFTED", "DECAYING", "SHADOW_RESULT", "NOTE")

SCHEMA = """
CREATE SEQUENCE IF NOT EXISTS lesson_event_seq START 1;
CREATE TABLE IF NOT EXISTS lesson_events (
    seq BIGINT PRIMARY KEY DEFAULT nextval('lesson_event_seq'), ts TIMESTAMP NOT NULL, lesson_id VARCHAR NOT NULL,
    kind VARCHAR NOT NULL, session VARCHAR NOT NULL DEFAULT '', actor VARCHAR NOT NULL, payload VARCHAR NOT NULL,
    UNIQUE (lesson_id, kind, session));
CREATE TABLE IF NOT EXISTS autopsies (
    trade_id VARCHAR, mode VARCHAR, day VARCHAR, symbol VARCHAR, strategy VARCHAR, side VARCHAR, net_pnl DOUBLE,
    gross_pnl DOUBLE, net_bp DOUBLE, r_gross DOUBLE, mae_r DOUBLE, mfe_r DOUBLE, minutes_in INTEGER, exit_reason VARCHAR,
    reversed_after_exit BOOLEAN, has_path BOOLEAN, classes VARCHAR, primary_class VARCHAR, PRIMARY KEY (trade_id, mode));
CREATE TABLE IF NOT EXISTS incidents (
    incident_id VARCHAR PRIMARY KEY, day VARCHAR, source VARCHAR, cause VARCHAR, severity VARCHAR, detail VARCHAR,
    detected_by VARCHAR, first_seen VARCHAR);
CREATE TABLE IF NOT EXISTS loop_runs (
    run_on VARCHAR PRIMARY KEY, session VARCHAR, sessions INTEGER, trades INTEGER, nominated INTEGER, confirmed INTEGER,
    refuted INTEGER, decaying INTEGER, status VARCHAR, note VARCHAR);
"""


class StoreError(ValueError):
    pass


def fold(events) -> dict[str, dict]:
    """Lessons from an ordered event stream. Pure, so the nightly job and the calibration simulator share one definition."""
    out: dict[str, dict] = {}
    for e in events:
        x = out.setdefault(e["lesson_id"], {"id": e["lesson_id"], "stage": None, "events": [], "nomination": None,
                                            "decision": None, "shadow": None, "approval": None, "decaying": None})
        x["events"].append(e)
        if e["kind"] in STAGE_OF:
            x["stage"] = STAGE_OF[e["kind"]]
        if e["kind"] == "NOMINATED":
            x["nomination"] = e["payload"]
            x["cell"] = e["payload"].get("cell")
        elif e["kind"] in ("CONFIRMED", "REFUTED", "EXPIRED"):
            x["decision"] = {**e["payload"], "kind": e["kind"], "session": e["session"]}
        elif e["kind"] == "REGISTERED":
            x.setdefault("cell", e["payload"].get("cell"))
        elif e["kind"] == "SHADOW_RESULT":
            x["shadow"] = {**e["payload"], "session": e["session"]}
        elif e["kind"] == "APPROVED":
            x["approval"] = e["payload"]
        elif e["kind"] == "DECAYING":
            x["decaying"] = {**e["payload"], "session": e["session"]}
    return out


class Store:
    def __init__(self, path: Path | str = PATH, *, read_only: bool = False):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(self.path, read_only=read_only)
        if not read_only:
            self.con.execute(SCHEMA)

    def close(self) -> None:
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------------------------------- events

    def append(self, lesson_id: str, kind: str, *, session: str = "", actor: str = "system", payload: dict | None = None,
               ts: datetime | None = None) -> bool:
        """Add one event. Returns False (and writes nothing) when the same ``(lesson_id, kind, session)`` exists."""
        if kind not in KINDS:
            raise StoreError(f"unknown event kind {kind!r}")
        before = self.con.execute("SELECT COUNT(*) FROM lesson_events").fetchone()[0]
        self.con.execute("""INSERT INTO lesson_events (ts, lesson_id, kind, session, actor, payload)
                            VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
                         [ts or datetime.now(), lesson_id, kind, session, actor,
                          json.dumps(payload or {}, default=str, sort_keys=True)])
        return self.con.execute("SELECT COUNT(*) FROM lesson_events").fetchone()[0] > before

    def events(self, lesson_id: str | None = None) -> list[dict]:
        q = "SELECT seq, ts, lesson_id, kind, session, actor, payload FROM lesson_events"
        rows = self.con.execute(q + (" WHERE lesson_id = ?" if lesson_id else "") + " ORDER BY seq",
                                [lesson_id] if lesson_id else []).fetchall()
        return [{"seq": r[0], "ts": r[1], "lesson_id": r[2], "kind": r[3], "session": r[4], "actor": r[5],
                 "payload": json.loads(r[6])} for r in rows]

    def lessons(self) -> dict[str, dict]:
        """Every lesson folded from its events: stage, the frozen nomination, the decision, the latest shadow verdict."""
        return fold(self.events())

    # ------------------------------------------------------------------------------------------- derived tables

    def replace_autopsies(self, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        self.con.register("a_df", df)
        try:
            self.con.execute("DELETE FROM autopsies WHERE (trade_id, mode) IN (SELECT trade_id, mode FROM a_df)")
            cols = [r[0] for r in self.con.execute("SELECT column_name FROM information_schema.columns "
                                                    "WHERE table_name = 'autopsies' ORDER BY ordinal_position").fetchall()]
            self.con.execute(f"INSERT INTO autopsies SELECT {', '.join(cols)} FROM a_df")
        finally:
            self.con.unregister("a_df")
        return len(df)

    def autopsies(self) -> pd.DataFrame:
        return self.con.execute("SELECT * FROM autopsies ORDER BY day, trade_id").df()

    def add_incidents(self, rows: list[dict]) -> int:
        n = 0
        for r in rows:
            before = self.con.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
            self.con.execute("""INSERT INTO incidents VALUES (?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
                             [r["incident_id"], r["day"], r["source"], r["cause"], r["severity"], r["detail"][:400],
                              r.get("detected_by", ""), r.get("first_seen", datetime.now().date().isoformat())])
            n += self.con.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] > before
        return n

    def incidents(self) -> pd.DataFrame:
        return self.con.execute("SELECT * FROM incidents ORDER BY day, source, cause").df()

    def record_run(self, **kw) -> None:
        self.con.execute("INSERT OR REPLACE INTO loop_runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                         [kw["run_on"], kw.get("session", ""), kw.get("sessions", 0), kw.get("trades", 0),
                          kw.get("nominated", 0), kw.get("confirmed", 0), kw.get("refuted", 0), kw.get("decaying", 0),
                          kw.get("status", ""), kw.get("note", "")])

    def runs(self) -> pd.DataFrame:
        return self.con.execute("SELECT * FROM loop_runs ORDER BY run_on").df()
