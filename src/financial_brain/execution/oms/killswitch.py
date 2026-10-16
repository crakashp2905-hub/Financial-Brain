"""The broker kill switch: when it trips, open orders are cancelled at the broker and no new order is sent.

Unlike ``risk/killswitch.py`` (which stops the *paper* loop), this one acts on whatever broker the OMS holds. It is
a file (``data/live/killswitch.json``) so every process - the OMS loop, a CLI, the evening job - sees the same state,
and ``kite_broker.locks`` reads it before each order.

    trip(reason, broker)   writes the state first, then cancels every live order it can see at the broker
    reset(actor, note)     only a human: an actor starting with ``agent:`` is refused

Tripping writes first and cancels second, so a crash between the two leaves the system blocked, never permissive.
Flattening positions is deliberately not automatic: selling into a fault can be worse than holding; the runbook
says when a human should flatten.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import model as M

PATH = Path("data") / "live" / "killswitch.json"


class KillSwitchError(PermissionError):
    pass


def load(path: Path | None = None) -> dict:
    p = Path(path or PATH)
    if not p.exists():
        return {"tripped": False}
    return json.loads(p.read_text(encoding="utf-8"))


def _write(state: dict, path: Path | None) -> None:
    p = Path(path or PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, default=str), encoding="utf-8")
    tmp.replace(p)                                       # atomic on the same volume


def trip(reason: str, broker=None, *, path: Path | None = None, journal=None) -> dict:
    cancelled: list[str] = []
    errors: list[str] = []
    state = {"tripped": True, "reason": reason, "at": datetime.now(timezone.utc).isoformat(), "cancelled": cancelled,
             "cancel_errors": errors}
    _write(state, path)
    if broker is not None:
        try:
            live = [b for b in broker.orders() if b.state in (M.OPEN, M.PARTIAL)]
        except Exception as e:                           # noqa: BLE001 - the switch stays tripped regardless
            errors.append(f"could not list orders: {e}")
            live = []
        for b in live:
            try:
                broker.cancel(b.broker_id)
                cancelled.append(b.broker_id)
            except Exception as e:                       # noqa: BLE001
                errors.append(f"{b.broker_id}: {e}")
        _write(state, path)
    if journal is not None:
        journal.note("KILL_SWITCH_TRIPPED", state, key=f"ks|{state['at']}")
    return state


def reset(actor: str, note: str, *, path: Path | None = None, journal=None) -> dict:
    if not actor or actor.startswith("agent:"):
        raise KillSwitchError("only a human may reset the broker kill switch")
    prev = load(path)
    state = {"tripped": False, "reset_by": actor, "note": note, "at": datetime.now(timezone.utc).isoformat(),
             "previous": prev}
    _write(state, path)
    if journal is not None:
        journal.note("KILL_SWITCH_RESET", state, key=f"ksr|{state['at']}")
    return state
