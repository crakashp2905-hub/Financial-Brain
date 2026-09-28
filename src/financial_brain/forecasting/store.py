"""Persisting forecasts, and resolving them once the future arrives.

A forecast that is not written down cannot be scored later, and a forecast scored at the moment it is
made cannot be scored at all. The loop this table closes is:

    forecast -> (the horizon elapses) -> realised price -> score -> calibration

The design decisions worth stating, because each one has an obvious cheaper alternative that breaks
something:

**The paths are stored, not the quantiles.** Two forecasts with identical terminal quantiles can have
opposite first-passage probabilities, because that answer depends on the order prices arrive in. A
table of p05..p95 cannot answer "is the target reached before the stop", which is the only question a
position with a stop asks.

**A forecast whose horizon has not elapsed is distinct from one with no realised price.** Both have
``realised IS NULL``, and collapsing them is how a study drops its most recent observations - which
are the ones most likely to be unfavourable, since a model is usually deployed after a good backtest.
:func:`resolve` fills in only the forecasts whose horizon has genuinely closed, and reports the two
counts separately.

**The forecast id hashes the configuration.** A temperature that happened to work is a searched
parameter. Two forecasts of the same name on the same date at different temperatures are different
forecasts, and an id that collapsed them would let the better one be presented as the record.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

from . import calibration as C
from .distribution import ForecastDistribution, ForecastError
from .walkforward import realised as realised_close


def forecast_id(fc: ForecastDistribution) -> str:
    """A stable hash of what produced the forecast, so two runs of it collide and nothing else does.

    The sampled paths are deliberately *not* hashed: a stochastic model draws different paths each
    time, and an id that changed with them would make every forecast unique and the table
    append-only-by-accident. The configuration is what identifies the experiment; the paths are its
    output.
    """
    body = json.dumps({
        "model": fc.model,
        "model_version": fc.model_version,
        "lineage": fc.instrument,
        "as_of": str(fc.as_of),
        "horizon": fc.horizon,
        "data_version": fc.data_version,
        # Only the knobs, not the derived numbers - meta carries both.
        "config": {k: str(v) for k, v in sorted(fc.meta.items())
                   if k in {"temperature", "top_p", "seed", "context", "lookback",
                            "mean_block", "device"}},
    }, sort_keys=True)
    return "FC-" + hashlib.sha256(body.encode()).hexdigest()[:16]


def save(con, fc: ForecastDistribution) -> str:
    """Write one forecast. Re-saving the same configuration replaces its paths rather than duplicating.

    A replace rather than an insert-ignore: re-running a stochastic model legitimately produces new
    paths for the same experiment, and keeping the first draw forever would mean a re-run silently had
    no effect.
    """
    fid = forecast_id(fc)
    con.execute("DELETE FROM forecast_distributions WHERE forecast_id = ?", [fid])
    con.execute("""
        INSERT INTO forecast_distributions
        (forecast_id, lineage, as_of, horizon, model, model_version, made_at, anchor,
         n_paths, paths, config, data_version, realised, realised_at, scored_at, crps, pit)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL)
    """, [fid, fc.instrument, fc.as_of, fc.horizon, fc.model, fc.model_version,
          datetime.now(timezone.utc), fc.anchor, fc.n_paths, fc.paths,
          json.dumps({k: str(v) for k, v in sorted(fc.meta.items())}), fc.data_version])
    return fid


def load(con, fid: str) -> ForecastDistribution:
    row = con.execute("""
        SELECT lineage, as_of, horizon, anchor, paths, model, model_version, data_version, config
        FROM forecast_distributions WHERE forecast_id = ?""", [fid]).fetchone()
    if row is None:
        raise ForecastError(f"no forecast {fid}")
    return ForecastDistribution(
        instrument=row[0], as_of=row[1], horizon=row[2], anchor=row[3],
        paths=[list(p) for p in row[4]], model=row[5], model_version=row[6] or "",
        data_version=row[7] or "", meta=json.loads(row[8]) if row[8] else {})


def resolve(con, *, as_of: date | None = None, limit: int | None = None) -> dict:
    """Fill in realised prices and scores for every forecast whose horizon has closed.

    Returns counts for three outcomes, kept apart on purpose:

    * ``scored`` - the horizon elapsed and a price was found.
    * ``pending`` - the horizon has not elapsed yet. Not a failure, and not evidence about the model.
    * ``unresolvable`` - the horizon elapsed and there is still no price, which means the lineage
      stopped trading. That is information about the name, and counting it as ``pending`` would leave
      it waiting forever.
    """
    rows = con.execute("""
        SELECT forecast_id, lineage, as_of, horizon
        FROM forecast_distributions
        WHERE realised IS NULL AND scored_at IS NULL
        ORDER BY as_of
        """ + (f"LIMIT {int(limit)}" if limit else ""), []).fetchall()

    scored = pending = unresolvable = 0
    for fid, lineage, fc_as_of, horizon in rows:
        price = realised_close(con, lineage, fc_as_of, horizon)
        if price is None:
            # Has the horizon even closed? If sessions exist beyond the window the price should have
            # landed in, the absence is real; otherwise it is simply too early.
            beyond = con.execute("""
                SELECT COUNT(*) FROM (SELECT DISTINCT business_date FROM adjusted_prices
                                      WHERE business_date > ?)""", [fc_as_of]).fetchone()[0]
            if beyond >= horizon:
                unresolvable += 1
            else:
                pending += 1
            continue
        fc = load(con, fid)
        sample = fc.terminal()
        con.execute("""
            UPDATE forecast_distributions
            SET realised = ?, realised_at = ?, scored_at = ?, crps = ?, pit = ?
            WHERE forecast_id = ?""",
            [price, as_of or date.today(), datetime.now(timezone.utc),
             C.crps(sample, price) if len(sample) > 1 else None,
             C.pit(sample, price) if len(sample) > 1 else None, fid])
        scored += 1
    return {"considered": len(rows), "scored": scored, "pending": pending,
            "unresolvable": unresolvable,
            "why": "pending means the horizon has not closed; unresolvable means it has and the "
                   "lineage stopped trading. Collapsing them drops the most recent observations."}


def scored(con, *, model: str | None = None, horizon: int | None = None) -> dict:
    """Aggregate the resolved forecasts. Calibration from stored PITs, accuracy from stored CRPS."""
    where = ["scored_at IS NOT NULL", "crps IS NOT NULL"]
    args: list = []
    if model:
        where.append("model = ?")
        args.append(model)
    if horizon:
        where.append("horizon = ?")
        args.append(horizon)
    clause = " AND ".join(where)
    row = con.execute(f"""SELECT COUNT(*), AVG(crps), MIN(as_of), MAX(as_of),
                                 COUNT(DISTINCT lineage), COUNT(DISTINCT as_of)
                          FROM forecast_distributions WHERE {clause}""", args).fetchone()
    pits = [r[0] for r in con.execute(
        f"SELECT pit FROM forecast_distributions WHERE {clause} AND pit IS NOT NULL",
        args).fetchall()]
    return {
        "n": row[0], "crps": row[1], "first": row[2], "last": row[3],
        "lineages": row[4], "sessions": row[5],
        "calibration": C._uniformity(pits) if pits else {"chi2": None, "n": 0},
        "model": model, "horizon": horizon,
    }
