"""The paper loop's off switch: it trips on evidence and only a human resets it.

Nothing here touches a broker - there is no broker in this system's reach. What it stops is the *paper*
book: when it trips, the daily loop proposes nothing, because a paper track record accumulated while the
process is visibly broken is worse than none.

## What trips it

    DRAWDOWN        the paper book is more than ``max_drawdown`` below its peak
    DAILY_LOSS      one session lost more than ``max_daily_loss``
    STALE_DATA      the newest price is more than ``max_stale_sessions`` sessions old
    DEGRADED        the signal-degradation monitor says the edge has gone
    COST_OVERRUN    realised slippage has run above ``cost_overrun`` times what was modelled

Two levels, because a system that can only be fully on or fully off gets switched off at the first
false alarm and then ignored: **REDUCE** (exposure scaled to 0.5) for a soft breach, **HALT** (nothing
proposed) for a hard one.

## Who can reset it

A trip is written to a state file and stays until **a human** clears it. ``reset`` refuses any actor
whose name starts with ``agent:``, the same rule the decision ledger applies to approval. A switch an
agent could un-trip is not a switch.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

OK, REDUCE, HALT = "OK", "REDUCE", "HALT"
DEFAULT_STATE = Path("data") / "killswitch.json"


class KillSwitchError(ValueError):
    pass


@dataclass(frozen=True)
class Limits:
    max_drawdown: float = 0.12             #: HALT beyond this peak-to-trough fall
    reduce_drawdown: float = 0.06          #: REDUCE beyond this
    max_daily_loss: float = 0.04
    max_stale_sessions: int = 3
    cost_overrun: float = 2.0


def drawdown(equity: list[float]) -> float:
    peak, worst = float("-inf"), 0.0
    for e in equity:
        peak = max(peak, e)
        worst = max(worst, (peak - e) / peak if peak > 0 else 0.0)
    return worst


def evaluate(*, equity: list[float] | None = None, last_data: date | None = None,
             today: date | None = None, sessions_since_data: int | None = None,
             degraded: bool = False, realised_slippage: float | None = None,
             modelled_slippage: float | None = None, limits: Limits = Limits()) -> dict:
    """The verdict for today. Every input is optional; a missing one is simply not checked, and the
    verdict says which checks ran, so silence is never mistaken for health."""
    reasons, ran = [], []
    level = OK

    def raise_to(new: str):
        nonlocal level
        order = {OK: 0, REDUCE: 1, HALT: 2}
        if order[new] > order[level]:
            level = new

    if equity and len(equity) >= 2:
        ran.append("drawdown")
        dd = drawdown(equity)
        if dd > limits.max_drawdown:
            reasons.append(f"DRAWDOWN {dd:.1%} > {limits.max_drawdown:.0%}")
            raise_to(HALT)
        elif dd > limits.reduce_drawdown:
            reasons.append(f"DRAWDOWN {dd:.1%} > {limits.reduce_drawdown:.0%}")
            raise_to(REDUCE)
        ran.append("daily_loss")
        day = equity[-1] / equity[-2] - 1
        if day < -limits.max_daily_loss:
            reasons.append(f"DAILY_LOSS {day:.1%}")
            raise_to(HALT)
    stale = sessions_since_data
    if stale is None and last_data and today:
        stale = (today - last_data).days * 5 // 7
    if stale is not None:
        ran.append("stale_data")
        if stale > limits.max_stale_sessions:
            reasons.append(f"STALE_DATA {stale} sessions old")
            raise_to(HALT)
    if degraded:
        ran.append("degradation")
        reasons.append("DEGRADED signal edge")
        raise_to(REDUCE)
    if realised_slippage is not None and modelled_slippage:
        ran.append("cost_overrun")
        if realised_slippage > limits.cost_overrun * modelled_slippage:
            reasons.append(f"COST_OVERRUN {realised_slippage / modelled_slippage:.1f}x modelled")
            raise_to(REDUCE)
    return {"level": level, "reasons": reasons, "checks_run": ran,
            "exposure_scale": {OK: 1.0, REDUCE: 0.5, HALT: 0.0}[level]}


def load(path: Path = DEFAULT_STATE) -> dict:
    p = Path(path)
    if not p.exists():
        return {"level": OK, "reasons": [], "tripped_at": None}
    return json.loads(p.read_text(encoding="utf-8"))


def trip(verdict: dict, path: Path = DEFAULT_STATE) -> dict:
    """Persist a non-OK verdict. A worse standing trip is never downgraded by a milder verdict."""
    cur = load(path)
    order = {OK: 0, REDUCE: 1, HALT: 2}
    if order[verdict["level"]] <= order[cur["level"]] and cur["level"] != OK:
        return cur
    if verdict["level"] == OK:
        return cur
    state = {"level": verdict["level"], "reasons": verdict["reasons"],
             "tripped_at": datetime.now(timezone.utc).isoformat()}
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state), encoding="utf-8")
    return state


def reset(actor: str, note: str, path: Path = DEFAULT_STATE) -> dict:
    """Clear a trip. Humans only, with a reason that is kept."""
    if not actor or actor.startswith("agent:"):
        raise KillSwitchError("only a human can reset the kill switch")
    if not note or len(note.strip()) < 10:
        raise KillSwitchError("say why it is safe to resume (10+ characters)")
    prev = load(path)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"level": OK, "reasons": [], "tripped_at": None,
                             "reset_by": actor, "reset_note": note.strip(),
                             "previous": prev}), encoding="utf-8")
    return load(path)


def exposure_scale(path: Path = DEFAULT_STATE) -> float:
    return {OK: 1.0, REDUCE: 0.5, HALT: 0.0}[load(path)["level"]]
