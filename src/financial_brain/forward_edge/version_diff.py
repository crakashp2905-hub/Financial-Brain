"""Content-addressed, reviewable diffs between frozen strategy identities (PE-0058)."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass

_SHA = re.compile(r"^[0-9a-f]{64}$")


class StrategyVersionDiffError(ValueError):
    pass


@dataclass(frozen=True)
class StrategyVersionSnapshot:
    strategy_id: str
    version: int
    code_sha256: str
    parameters_sha256: str
    filters_sha256: str
    horizon_sessions: int
    universe_sha256: str
    cost_model_sha256: str
    benchmark_sha256: str
    asset_scope: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.strategy_id.strip() or self.version < 1 or self.horizon_sessions < 1:
            raise StrategyVersionDiffError("strategy identity needs an id, positive version, and horizon")
        for key in ("code_sha256", "parameters_sha256", "filters_sha256", "universe_sha256",
                    "cost_model_sha256", "benchmark_sha256"):
            if not _SHA.fullmatch(getattr(self, key)):
                raise StrategyVersionDiffError(f"{key} must be a lowercase SHA-256 digest")
        if not self.asset_scope or any(not isinstance(item, str) or not item.strip() for item in self.asset_scope):
            raise StrategyVersionDiffError("asset scope must contain nonblank values")
        object.__setattr__(self, "asset_scope", tuple(sorted(set(item.strip() for item in self.asset_scope))))

    def sha256(self) -> str:
        body = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


_CATEGORIES = {
    "code_sha256": "strategy_code",
    "parameters_sha256": "parameters",
    "filters_sha256": "filters",
    "horizon_sessions": "economic_horizon",
    "universe_sha256": "universe",
    "asset_scope": "asset_scope",
    "cost_model_sha256": "execution_cost_model",
    "benchmark_sha256": "benchmark",
}


def diff_strategy_versions(previous: StrategyVersionSnapshot,
                           current: StrategyVersionSnapshot) -> dict:
    """Return a stable diff; version changes without identity changes are called out explicitly."""
    if previous.strategy_id != current.strategy_id:
        raise StrategyVersionDiffError("versions must belong to the same strategy id")
    if current.version != previous.version + 1:
        raise StrategyVersionDiffError("strategy version must increase by exactly one")
    before, after = asdict(previous), asdict(current)
    changes = [
        {"field": key, "category": _CATEGORIES[key], "before": before[key], "after": after[key]}
        for key in _CATEGORIES if before[key] != after[key]
    ]
    result = {
        "strategy_id": previous.strategy_id,
        "from_version": previous.version,
        "to_version": current.version,
        "before_sha256": previous.sha256(),
        "after_sha256": current.sha256(),
        "changes": changes,
        "scientific_change": bool(changes),
        "version_only": not changes,
    }
    canonical = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    result["diff_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return result
