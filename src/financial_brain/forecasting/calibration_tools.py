"""Calibration, nulls, strata and material-change controls (FB-2205, FB-2206, FB-2401, FB-2414,
FB-2415, FB-2428, FB-2429).

* ``isotonic``: pool-adjacent-violators calibration fitted on a held-out *earlier* window.
* ``strata_calibrate``: per-stratum calibration from prior data only; thin strata stay UNCALIBRATED.
* ``nulls``: always-up, class-frequency, momentum and no-trade forecasts to beat.
* ``by_stratum``: metrics split into expiry/non-expiry, vol regimes and indices.
* ``material_change``: a vendor, latency class, size, cost or TTL change starts a new version.
* ``IndexMaster``: all Nifty indices with dated names, family, token and coverage; renamed or
  discontinued series are kept.
* ``compare_to_baselines``: a model is reported against simple baselines, never alone.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date


class CalError(ValueError):
    pass


def isotonic(scores: Sequence[float], outcomes: Sequence[int]) -> list[tuple[float, float]]:
    """Return (score_upper_bound, calibrated_probability) steps."""
    if len(scores) != len(outcomes) or not scores:
        raise CalError("need matching non-empty inputs")
    pts = sorted(zip(scores, outcomes, strict=True))
    blocks: list[list[float]] = []      # [sum_y, count, max_score]
    for s, y in pts:
        blocks.append([float(y), 1.0, s])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            y2, c2, s2 = blocks.pop()
            blocks[-1][0] += y2
            blocks[-1][1] += c2
            blocks[-1][2] = s2
    return [(b[2], b[0] / b[1]) for b in blocks]


def apply_isotonic(steps: list[tuple[float, float]], score: float) -> float:
    for ub, p in steps:
        if score <= ub:
            return p
    return steps[-1][1]


def strata_calibrate(rows: list[tuple[str, float, int, date]], cutoff: date, min_n: int
                     ) -> dict[str, list[tuple[float, float]] | str]:
    """Fit per stratum on rows dated strictly before ``cutoff``; thin strata are UNCALIBRATED."""
    by: dict[str, list[tuple[float, int]]] = {}
    for stratum, s, y, d in rows:
        if d < cutoff:
            by.setdefault(stratum, []).append((s, y))
    out: dict[str, list[tuple[float, float]] | str] = {}
    for k, v in by.items():
        out[k] = "UNCALIBRATED" if len(v) < min_n else isotonic([a for a, _ in v], [b for _, b in v])
    return out


def nulls(train_labels: Sequence[str], recent_returns: Sequence[float]) -> dict[str, dict[str, float]]:
    c = Counter(train_labels)
    n = sum(c.values()) or 1
    freq = {k: c.get(k, 0) / n for k in ("UP", "DOWN", "FLAT")}
    mom = "UP" if recent_returns and recent_returns[-1] > 0 else "DOWN"
    return {"always_up": {"UP": 1.0, "DOWN": 0.0, "FLAT": 0.0}, "class_frequency": freq,
            "momentum": {k: 1.0 if k == mom else 0.0 for k in ("UP", "DOWN", "FLAT")},
            "no_trade": {"UP": 0.0, "DOWN": 0.0, "FLAT": 1.0}}


def by_stratum(rows: list[dict], key: str, metric: str) -> dict[str, float]:
    g: dict[str, list[float]] = {}
    for r in rows:
        g.setdefault(str(r[key]), []).append(float(r[metric]))
    return {k: sum(v) / len(v) for k, v in sorted(g.items())}


MATERIAL = ("vendor", "latency_class", "order_size", "cost_model", "signal_ttl")


def material_change(old: dict, new: dict) -> list[str]:
    """Fields whose change breaks comparability and forces a new experiment version."""
    return [k for k in MATERIAL if old.get(k) != new.get(k)]


@dataclass(frozen=True)
class IndexVersion:
    effective_from: date
    name: str
    symbol: str
    family: str
    token: str | None
    status: str = "LIVE"        # LIVE, RENAMED, DISCONTINUED


@dataclass
class IndexMaster:
    rows: dict[str, list[IndexVersion]] = field(default_factory=dict)
    coverage: dict[str, tuple[date, date]] = field(default_factory=dict)

    def add(self, index_id: str, v: IndexVersion) -> None:
        self.rows.setdefault(index_id, []).append(v)
        self.rows[index_id].sort(key=lambda x: x.effective_from)

    def as_of(self, index_id: str, on: date) -> IndexVersion | None:
        hits = [v for v in self.rows.get(index_id, []) if v.effective_from <= on]
        return hits[-1] if hits else None

    def forecastable(self, on: date) -> list[str]:
        out = []
        for k in self.rows:
            v = self.as_of(k, on)
            cov = self.coverage.get(k)
            if v and v.status != "DISCONTINUED" and cov and cov[0] <= on <= cov[1]:
                out.append(k)
        return sorted(out)


def compare_to_baselines(model_score: float, baselines: dict[str, float], lower_is_better: bool = True
                         ) -> dict[str, float]:
    """Model minus each baseline (negative is better for loss metrics)."""
    sign = 1 if lower_is_better else -1
    return {k: sign * (model_score - v) for k, v in baselines.items()}
