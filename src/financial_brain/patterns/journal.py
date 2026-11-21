"""Paper session journal: wizard, frozen manifests, intent states, marks, summaries and replay (stage 17).

Every simulated result is labelled SIMULATED. The manifest identity is a hash of its
full contents, so any change alters identity and an existing manifest cannot be
overwritten silently (CP-0267).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from financial_brain.patterns.spec import canonical_json, sha256_hex

LABEL = "SIMULATED"


class ManifestConflict(RuntimeError):
    """A manifest with the same id but different contents already exists."""


def new_session(*, capital: float, start: str, end: str, strategy_filter: tuple[str, ...], fill_policy: str) -> dict[str, Any]:
    """Wizard output: validates inputs, runs offline, and returns a frozen manifest body (CP-0266)."""
    if not math.isfinite(capital) or capital <= 0:
        raise ValueError("capital must be a positive finite number")
    if start > end:
        raise ValueError("start must not follow end")
    if fill_policy not in ("next_session", "none"):
        raise ValueError("fill_policy must be next_session or none")
    return {
        "label": LABEL,
        "capital": round(capital, 2),
        "start": start,
        "end": end,
        "strategy_filter": sorted(strategy_filter),
        "fill_policy": fill_policy,
        "network": False,
    }


def manifest_identity(body: dict[str, Any]) -> str:
    return "man_" + sha256_hex(canonical_json(body))[:32]


def save_manifest(body: dict[str, Any], folder: Path) -> Path:
    """Write a manifest; an existing file with different contents is refused (CP-0267)."""
    folder.mkdir(parents=True, exist_ok=True)
    identity = manifest_identity(body)
    path = folder / f"{identity}.json"
    text = json.dumps(body, sort_keys=True, indent=2) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise ManifestConflict(f"{path.name} exists with different contents")
        return path
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)  # atomic within the folder (CP-0366)
    return path


INTENT_STATES = ("PENDING", "REJECTED", "EXPIRED", "UNFILLED", "PARTIALLY_FILLED", "FULLY_FILLED")


@dataclass
class IntentLedger:
    """Every intent and its state; coverage reconciles intents with fills (CP-0270)."""

    states: dict[str, str] = field(default_factory=dict)

    def set_state(self, intent_id: str, state: str) -> None:
        if state not in INTENT_STATES:
            raise ValueError(f"unknown intent state {state!r}")
        self.states[intent_id] = state

    def coverage(self) -> dict[str, int]:
        out = {s: 0 for s in INTENT_STATES}
        for s in self.states.values():
            out[s] += 1
        out["total"] = len(self.states)
        return out


@dataclass(frozen=True)
class DailyMark:
    day: str
    cash: float
    position_value: float
    liabilities: float

    @property
    def equity(self) -> float:
        return self.cash + self.position_value - self.liabilities


@dataclass(frozen=True)
class ClosedTrade:
    symbol: str
    entry: float
    exit: float
    quantity: int
    costs: float

    @property
    def realized(self) -> float:
        return (self.exit - self.entry) * self.quantity - self.costs


def dump_intents(ledger: IntentLedger) -> tuple[str, str]:
    """Backup of intent states with a checksum (CP-0390)."""
    text = json.dumps(ledger.states, sort_keys=True, separators=(",", ":"))
    return text, sha256_hex(text.encode("utf-8"))


def load_intents(text: str, checksum: str) -> IntentLedger:
    """Restore intent states; a checksum mismatch refuses the restore and leaves states untouched."""
    if sha256_hex(text.encode("utf-8")) != checksum:
        raise ValueError("intent backup checksum mismatch; refusing to restore")
    ledger = IntentLedger()
    for intent_id, state in json.loads(text).items():
        ledger.set_state(intent_id, state)
    return ledger


def pnl_split(closed: list[ClosedTrade], open_marks: dict[str, tuple[float, float, int]]) -> dict[str, float]:
    """Realised (closed trades only) and unrealised (open, marked) reported separately (CP-0272)."""
    realised = sum(t.realized for t in closed)
    unrealised = sum((mark - entry) * qty for entry, mark, qty in open_marks.values())
    return {"realized": realised, "unrealized": unrealised, "total": realised + unrealised}


def period_summary(marks: list[DailyMark]) -> dict[str, float]:
    """Daily, weekly and monthly statistics from marks with an explicit denominator (CP-0274)."""
    if not marks:
        return {"days": 0, "total_return": 0.0, "max_drawdown": 0.0}
    start, end = marks[0].equity, marks[-1].equity
    peak = -math.inf
    worst = 0.0
    for m in marks:
        peak = max(peak, m.equity)
        if peak > 0:
            worst = max(worst, (peak - m.equity) / peak)
    return {"days": float(len(marks)), "total_return": (end - start) / start if start else 0.0, "max_drawdown": worst}


def contribution(trades: list[tuple[str, float]]) -> dict[str, dict[str, float]]:
    """Pattern contribution by family. Descriptive attribution, not proof of an edge (CP-0275)."""
    out: dict[str, dict[str, float]] = {}
    for family, pnl in trades:
        bucket = out.setdefault(family, {"trades": 0.0, "total": 0.0})
        bucket["trades"] += 1
        bucket["total"] += pnl
    return dict(sorted(out.items()))


def replay_identity(manifest_body: dict[str, Any], results: dict[str, Any]) -> str:
    """Hash of inputs and results; replaying the same manifest must reproduce it exactly (CP-0276)."""
    return sha256_hex(canonical_json({"manifest": manifest_body, "results": results}))


def scenario_all_loss(entries: int) -> list[ClosedTrade]:
    """Every trade loses; the journal must show it, not hide it (CP-0280)."""
    return [ClosedTrade(f"L{i}", 100.0, 95.0, 10, 1.0) for i in range(entries)]


def scenario_no_signal() -> tuple[list[ClosedTrade], dict[str, float]]:
    """No signal at all: zero trades and zero realised P&L (CP-0280, CP-0200)."""
    return [], {"realized": 0.0, "unrealized": 0.0, "total": 0.0}
