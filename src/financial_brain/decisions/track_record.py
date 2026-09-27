"""Which source has earned influence, measured, by regime and horizon.

Every agent in an ensemble starts with equal weight and most systems leave it there forever. That
is a choice to ignore evidence: after fifty decisions there is a record of which source was right,
under what conditions, and how well-calibrated its confidence was, and none of it is used.

This module scores sources on their **claims**, not on the decisions those claims went into. A
fundamental analyst who was right while the portfolio gate refused the trade was still right, and
a system that scores it on P&L learns the wrong thing.

## The three things worth knowing, and they are different

**Hit rate** - how often the direction was right. The crudest and the least useful alone: a source
that is 90% right on tiny moves and catastrophically wrong on large ones has an excellent hit rate.

**Calibration** - when it says 0.8, does it happen 80% of the time? Scored by the **Brier score**,
`mean((p - outcome)^2)`, lower being better, and decomposed into *reliability* (are the stated
probabilities honest) and *resolution* (does it distinguish cases at all). A source that always
says 0.5 has perfect reliability and zero resolution - it is calibrated and useless, and only the
decomposition separates those.

**Conditional skill** - the same three numbers within each regime and each horizon. This is where
the usable finding lives, because sources are rarely uniformly good: a technical source may be
worth listening to in a trending regime and noise in a mean-reverting one, and averaging over
regimes hides exactly that.

## The bar this has to clear before it is used

**Fifty observations per cell.** A hit rate from six claims is not a track record and weighting an
ensemble by it is worse than equal weighting, because it converts noise into confidence. Every
number below carries its `n`, and `usable` is False until the cell is large enough. This project
has 25 closed paper trades in total, so the honest state today is that **no cell is usable** - the
machinery exists so it becomes usable as the record accumulates, not so it can be used now.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

#: Claims in a cell before its statistics may influence anything.
MIN_OBSERVATIONS = 50
#: Brier score of the base rate: a source must beat predicting the unconditional frequency.
#: Computed per cell rather than assumed, since the base rate is not 0.5.


@dataclass
class Outcome:
    """What happened to one claim, scored on the claim's own horizon."""
    claim_id: str
    source: str
    direction: int
    strength: float
    realised_return: float
    regime: str | None = None
    horizon_days: int | None = None
    resolved_on: date | None = None

    @property
    def correct(self) -> int:
        """Direction right. A neutral claim is scored right when the move was small, which is
        what neutral asserts - otherwise a source could never be wrong by abstaining."""
        if self.direction == 0:
            return 1 if abs(self.realised_return) < 0.02 else 0
        return 1 if self.direction * self.realised_return > 0 else 0


def ensure_table(con) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS claim_outcomes (
        claim_id VARCHAR, source VARCHAR, direction INTEGER, strength DOUBLE,
        realised_return DOUBLE, regime VARCHAR, horizon_days INTEGER,
        resolved_on DATE, recorded_at TIMESTAMP)""")


def record(con, outcomes: list[Outcome]) -> int:
    ensure_table(con)
    now = datetime.now(timezone.utc)
    con.executemany("""INSERT INTO claim_outcomes VALUES (?,?,?,?,?,?,?,?,?)""",
                    [(o.claim_id, o.source, o.direction, o.strength, o.realised_return,
                      o.regime, o.horizon_days, o.resolved_on, now) for o in outcomes])
    return len(outcomes)


def brier(pairs: list[tuple[float, int]]) -> dict:
    """Brier score with its reliability / resolution / uncertainty decomposition.

    `BS = reliability - resolution + uncertainty` (Murphy 1973). The decomposition is what makes
    the score readable: a source that always states the base rate has reliability 0 and
    resolution 0, scoring exactly `uncertainty` - calibrated, and carrying no information. Only
    resolution says whether it distinguishes anything.
    """
    if not pairs:
        return {"brier": float("nan"), "n": 0}
    n = len(pairs)
    base = sum(o for _, o in pairs) / n
    bs = sum((p - o) ** 2 for p, o in pairs) / n
    bins: dict = {}
    for p, o in pairs:
        bins.setdefault(round(min(max(p, 0.0), 1.0), 1), []).append(o)
    rel = sum(len(v) * (k - sum(v) / len(v)) ** 2 for k, v in bins.items()) / n
    res = sum(len(v) * (sum(v) / len(v) - base) ** 2 for k, v in bins.items()) / n
    unc = base * (1 - base)
    return {"brier": bs, "reliability": rel, "resolution": res, "uncertainty": unc,
            "base_rate": base, "n": n, "bins": len(bins),
            # Beating the base rate is the minimum bar. A source scoring worse than "always
            # predict the unconditional frequency" is actively harmful to an ensemble.
            "beats_base_rate": bs < unc,
            "skill_score": (1 - bs / unc) if unc > 0 else float("nan")}


def score(con, *, by: tuple[str, ...] = ("source",),
          min_observations: int = MIN_OBSERVATIONS) -> list[dict]:
    """Track records grouped by any of ``source``, ``regime``, ``horizon_days``.

    ``usable`` is the field that matters. A cell below ``min_observations`` is reported with its
    numbers and ``usable=False``, because seeing "hit rate 83% on 6 claims" alongside the count
    is informative, and using it to weight an ensemble is not.
    """
    if not con.execute("""SELECT COUNT(*) FROM duckdb_tables()
                          WHERE table_name = 'claim_outcomes'""").fetchone()[0]:
        return []
    allowed = {"source", "regime", "horizon_days"}
    if not set(by) <= allowed:
        raise ValueError(f"group by a subset of {sorted(allowed)}")
    cols = ", ".join(by)
    rows = con.execute(f"""
        SELECT {cols}, LIST(strength), LIST(direction), LIST(realised_return)
        FROM claim_outcomes GROUP BY {cols}
    """).fetchall()

    out = []
    for r in rows:
        keys = dict(zip(by, r[:len(by)]))
        strengths, directions, rets = r[len(by)], r[len(by) + 1], r[len(by) + 2]
        outs = [Outcome("", "", d, s, x) for s, d, x in
                zip(strengths, directions, rets)]
        correct = [o.correct for o in outs]
        pairs = [(o.strength, o.correct) for o in outs]
        b = brier(pairs)
        n = len(outs)
        # Mean return *in the claimed direction* - the economically meaningful version of a hit
        # rate, since being right on small moves and wrong on large ones is not skill.
        signed = [o.direction * o.realised_return for o in outs if o.direction != 0]
        out.append({
            **keys, "n": n,
            "hit_rate": sum(correct) / n if n else float("nan"),
            "mean_signed_return": (sum(signed) / len(signed)) if signed else float("nan"),
            "mean_strength": sum(strengths) / n if n else float("nan"),
            "brier": b["brier"], "reliability": b.get("reliability"),
            "resolution": b.get("resolution"), "skill_score": b.get("skill_score"),
            "beats_base_rate": b.get("beats_base_rate"),
            "usable": n >= min_observations,
            "why_not_usable": (None if n >= min_observations else
                               f"{n} observations, need {min_observations}; a hit rate from "
                               f"this few converts noise into confidence"),
        })
    out.sort(key=lambda d: (-d["n"], d.get("brier") or 1.0))
    return out


def weights(con, *, regime: str | None = None,
            min_observations: int = MIN_OBSERVATIONS) -> dict:
    """Influence weights per source, or equal weights when the record cannot support them.

    Deliberately conservative. Weights come from the **skill score** (1 − Brier/uncertainty) and
    only for cells that are both large enough and actually beat their base rate; everything else
    gets the equal weight it started with. A source is never given *more* than twice the equal
    weight however good its record looks, because the record is short and the estimate of skill
    is itself noisy.
    """
    cells = score(con, by=("source", "regime") if regime else ("source",),
                  min_observations=min_observations)
    if regime:
        cells = [c for c in cells if c.get("regime") == regime]
    if not cells:
        return {"weights": {}, "basis": "no record", "equal": True}
    sources = sorted({c["source"] for c in cells})
    eq = 1.0 / len(sources)
    usable = [c for c in cells if c["usable"] and c.get("beats_base_rate")
              and (c.get("skill_score") or 0) > 0]
    if not usable:
        return {"weights": {s: eq for s in sources}, "basis": "equal",
                "equal": True,
                "why": f"no source has {min_observations} observations that beat its base "
                       f"rate; weighting on less than that is worse than equal weighting"}
    raw = {c["source"]: min(c["skill_score"], 2 * eq / eq) for c in usable}
    for s in sources:
        raw.setdefault(s, eq)
    total = sum(raw.values())
    return {"weights": {s: v / total for s, v in raw.items()},
            "basis": "skill_score", "equal": False,
            "capped_at": "2x equal weight",
            "cells_used": [{k: c[k] for k in ("source", "n", "skill_score")}
                           for c in usable]}


def from_paper_trades(con) -> list[Outcome]:
    """Build claim outcomes from the closed paper ledger, attributing to the decision's author.

    A stopgap and labelled one: the ledger records *decisions*, not the individual claims that
    fed them, so this attributes a decision's outcome to its author rather than to each source
    that contributed. It becomes unnecessary once ``decisions/compile.py`` is the only path to a
    decision, because the compiler records every claim it received.
    """
    rows = con.execute("""
        SELECT p.decision_id, d.author, p.excess, p.entry_date, p.due_date,
               (SELECT r.regime FROM market_regime r WHERE r.business_date = p.entry_date)
        FROM paper_trades p LEFT JOIN decisions d USING (decision_id)
        WHERE p.status = 'closed' AND p.excess IS NOT NULL
    """).fetchall()
    return [Outcome(claim_id=r[0], source=r[1] or "unknown", direction=1,
                    strength=0.5, realised_return=r[2], regime=r[5],
                    horizon_days=((r[4] - r[3]).days if r[3] and r[4] else None),
                    resolved_on=r[4]) for r in rows]
