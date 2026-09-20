"""Runtime configuration.

Paths default to ``./data`` under the repository root so a fresh clone runs with no
setup. Override with the ``FB_DATA_ROOT`` environment variable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _root() -> Path:
    env = os.environ.get("FB_DATA_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return (Path(__file__).resolve().parents[2] / "data").resolve()


@dataclass(frozen=True)
class Config:
    data_root: Path

    @property
    def lake(self) -> Path:
        """Immutable raw landing zone. Write-once, never mutated."""
        return self.data_root / "lake"

    @property
    def curated(self) -> Path:
        """Parsed, validated Parquet datasets. Rebuildable from the lake."""
        return self.data_root / "curated"

    @property
    def quarantine(self) -> Path:
        """Payloads that failed a data-quality contract, kept for diagnosis."""
        return self.data_root / "quarantine"

    @property
    def db_path(self) -> Path:
        return self.data_root / "financial_brain.duckdb"

    def ensure(self) -> "Config":
        for p in (self.lake, self.curated, self.quarantine):
            p.mkdir(parents=True, exist_ok=True)
        return self


def load() -> Config:
    return Config(data_root=_root()).ensure()


# Source tiers, per docs/ARCHITECTURE.md "Source hierarchy". Tier 1 wins on conflict.
TIER = {
    "NSE": 1,
    "BSE": 1,
    "FILING": 1,
    "RBI": 1,
    "AMFI": 1,       # the industry body that publishes mutual-fund NAVs
    "KITE": 2,
    "SCREENER": 3,
    "MONEYCONTROL": 3,
    "NEWS": 3,
    "SOCIAL": 4,
    "DERIVED": 1,    # our deterministic computation over Tier-1 inputs (rules, checks)
    "MODEL": 4,      # a model's reading of a source: never outranks the source itself
}
