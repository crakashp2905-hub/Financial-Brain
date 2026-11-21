"""Time-series validation and search governance (FB-0145, FB-0146, FB-0457, FB-0458, FB-0459, FB-0460, FB-0461, FB-0462, FB-0463).

* ``walk_forward_splits``: train, then validate after an embargo. Training labels whose horizon reaches into the
  validation window are purged, so no training label contains a future validation price.
* ``neighbour_stability``: a best parameter whose neighbours collapse is flagged fragile.
* ``objective``: weights are fixed before search; the score is net return minus drawdown and turnover penalties.
* ``pareto_front``: non-dominated points among feasible candidates.
* ``seeded_random_search``: the same seed reproduces the same proposals; every attempt is returned for the ledger.
"""
from __future__ import annotations

import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Split:
    train: range
    validation: range


def walk_forward_splits(n: int, train: int, validate: int, *, embargo: int, horizon: int) -> list[Split]:
    """Rolling splits. Training indices whose label window (i .. i+horizon) crosses into validation are purged."""
    if min(train, validate, horizon) < 1 or embargo < 0:
        raise ValueError("window sizes must be positive and embargo non-negative")
    splits = []
    start = 0
    while start + train + embargo + validate <= n:
        v_start = start + train + embargo
        t_end = v_start - horizon  # purge labels that reach into the validation window
        if t_end <= start:
            raise ValueError("purge removes the whole training window; enlarge train or shrink horizon")
        splits.append(Split(range(start, t_end), range(v_start, v_start + validate)))
        start += validate
    return splits


def overlap_free(splits: Sequence[Split]) -> bool:
    for s in splits:
        if s.train and s.validation and s.train[-1] >= s.validation[0]:
            return False
    return True


def neighbour_stability(grid: Mapping[int, float], best_key: int, tolerance: float = 0.5) -> bool:
    """True when the best point's immediate neighbours keep at least ``tolerance`` of its score."""
    if best_key not in grid:
        raise KeyError("best key not in grid")
    best = grid[best_key]
    neighbours = [grid[k] for k in (best_key - 1, best_key + 1) if k in grid]
    if not neighbours:
        return False
    return all(v >= tolerance * best for v in neighbours)


@dataclass(frozen=True)
class Weights:
    net_return: float = 1.0
    drawdown: float = 0.5
    turnover: float = 0.1


FIXED_WEIGHTS = Weights()


def objective(net_return: float, max_drawdown: float, turnover: float, w: Weights = FIXED_WEIGHTS) -> float:
    return w.net_return * net_return - w.drawdown * max_drawdown - w.turnover * turnover


def pareto_front(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Maximise both coordinates; return the non-dominated set in input order."""
    front = []
    for i, p in enumerate(points):
        dominated = any(
            (q[0] >= p[0] and q[1] >= p[1]) and (q[0] > p[0] or q[1] > p[1])
            for j, q in enumerate(points) if j != i
        )
        if not dominated:
            front.append(p)
    return front


def seeded_random_search(
    space: Mapping[str, Sequence[float]],
    evaluate: Callable[[dict[str, float]], float],
    *,
    budget: int,
    seed: int,
) -> tuple[dict[str, float], float, list[tuple[dict[str, float], float]]]:
    """Fixed-budget random search. Returns (best params, best score, every attempt) so the search ledger is complete."""
    if budget < 1:
        raise ValueError("budget must be positive")
    rng = random.Random(seed)
    attempts: list[tuple[dict[str, float], float]] = []
    best: tuple[dict[str, float], float] | None = None
    for _ in range(budget):
        proposal = {k: rng.choice(list(v)) for k, v in sorted(space.items())}
        score = evaluate(proposal)
        attempts.append((proposal, score))
        if best is None or score > best[1]:
            best = (proposal, score)
    assert best is not None
    return best[0], best[1], attempts
