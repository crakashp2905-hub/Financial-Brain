"""Immutable strategy/code/parameter binding for prospective cohorts (FE-0090)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path


class ParameterFreezeError(PermissionError):
    """Prospective evaluation attempts to change its frozen strategy version."""


@dataclass(frozen=True)
class StrategyFreeze:
    cohort_id: str
    trial_id: str
    strategy_id: str
    code_hash: str
    parameters_hash: str
    model_hash: str
    frozen_at: datetime
    first_eligible_session: date

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.cohort_id, self.trial_id, self.strategy_id,
                                                self.code_hash, self.parameters_hash, self.model_hash)):
            raise ParameterFreezeError("cohort, trial, strategy and all version hashes are required")
        if self.frozen_at.tzinfo is None or self.frozen_at.utcoffset() is None:
            raise ParameterFreezeError("frozen_at must be timezone aware")
        if self.frozen_at.date() >= self.first_eligible_session:
            raise ParameterFreezeError("strategy must be frozen before first eligible session")

    def body(self) -> dict[str, str]:
        return {"cohort_id": self.cohort_id, "trial_id": self.trial_id,
                "strategy_id": self.strategy_id, "code_hash": self.code_hash,
                "parameters_hash": self.parameters_hash, "model_hash": self.model_hash,
                "frozen_at": self.frozen_at.astimezone(timezone.utc).isoformat(),
                "first_eligible_session": self.first_eligible_session.isoformat()}

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.body(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class StrategyFreezeRegistry:
    """Content-addressed cohort registry that refuses retrospective version changes."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("CREATE TABLE IF NOT EXISTS strategy_freezes (cohort_id TEXT PRIMARY KEY, digest TEXT NOT NULL, freeze_json TEXT NOT NULL)")
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def freeze(self, declaration: StrategyFreeze) -> bool:
        encoded = json.dumps(declaration.body(), sort_keys=True, separators=(",", ":"))
        prior = self._con.execute("SELECT digest,freeze_json FROM strategy_freezes WHERE cohort_id=?",
                                  [declaration.cohort_id]).fetchone()
        if prior:
            if prior != (declaration.digest(), encoded):
                raise ParameterFreezeError("cohort is immutable; changed strategy requires a new governed trial")
            return False
        self._con.execute("INSERT INTO strategy_freezes VALUES (?,?,?)",
                          [declaration.cohort_id, declaration.digest(), encoded])
        self._con.commit()
        return True

    def validate_run(self, *, cohort_id: str, session: date,
                     strategy_id: str, code_hash: str,
                     parameters_hash: str, model_hash: str) -> str:
        row = self._con.execute("SELECT freeze_json FROM strategy_freezes WHERE cohort_id=?", [cohort_id]).fetchone()
        if not row:
            raise ParameterFreezeError("unknown cohort; prospective evaluation is not authorized")
        frozen = json.loads(row[0])
        if session < date.fromisoformat(frozen["first_eligible_session"]):
            raise ParameterFreezeError("run precedes cohort eligibility")
        actual = {"strategy_id": strategy_id, "code_hash": code_hash,
                  "parameters_hash": parameters_hash, "model_hash": model_hash}
        expected = {key: frozen[key] for key in actual}
        if actual != expected:
            raise ParameterFreezeError("prospective strategy/model/parameters changed; new governed trial required")
        return str(self._con.execute("SELECT digest FROM strategy_freezes WHERE cohort_id=?", [cohort_id]).fetchone()[0])
