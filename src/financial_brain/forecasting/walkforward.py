"""Walk a forecaster across history, score it against the nulls, and enter it in the ledger.

The question this harness exists to answer is not "does the model predict prices". It is:

    does this model produce information about Indian securities that survives (a) being measured
    point-in-time, (b) comparison against a bootstrap of the name's own history, and (c) the
    multiple-testing bar that 150 previous trials have already raised?

All three matter and the third is the one that gets skipped. A forecast model is a trial. It goes in
``evaluation_runs`` next to ``mom_12_1`` and ``vol_60``, it is charged for the search that produced
it, and it faces the same Bonferroni bar - a 102M-parameter model is not exempt from arithmetic that
a moving-average crossover has to satisfy. :mod:`..evaluation.families` does the charging.

## Point-in-time discipline

Every forecast is conditioned on ``business_date <= as_of`` and nothing else, and the realised price
is read from the session exactly ``horizon`` trading sessions later - on the exchange calendar, not
by adding days, because a forecast scored 20 *calendar* days out is scored over a window whose length
depends on where the holidays fell.

The sample is drawn as a grid of (session, instrument) pairs. The grid is **strided**, not every
session, and this is not a performance compromise: overlapping horizons make observations dependent,
and 2,000 daily forecasts at a 20-session horizon carry roughly 100 independent observations. The
harness reports ``independent_observations`` alongside ``n`` so a t-statistic is never computed
against the wrong one.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date

from . import calibration as C
from .distribution import ForecastDistribution, ForecastError
from .null import nulls

VERSION = "fw-forecast-v1"

#: Sessions of history handed to a forecaster. Enough for the block bootstrap to have many distinct
#: blocks; more than most models can use, which is the forecaster's problem rather than the
#: harness's.
HISTORY = 750

#: Default stride between sample dates, in sessions. Equal to the horizon, so consecutive
#: observations do not share a single future return.
STRIDE_IS_HORIZON = object()


@dataclass
class Study:
    """One forecaster, walked across a grid, scored against every null on the same sample."""

    model: str
    horizon: int
    records: list[tuple[ForecastDistribution, float]] = field(default_factory=list)
    null_records: dict[str, list[tuple[ForecastDistribution, float]]] = field(
        default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    sessions: list[date] = field(default_factory=list)
    instruments: set[str] = field(default_factory=set)
    params: dict = field(default_factory=dict)

    @property
    def n(self) -> int:
        return len(self.records)

    @property
    def independent_observations(self) -> float:
        """Sample size after charging for overlapping horizons.

        Forecasts made ``stride`` sessions apart at horizon ``h`` share ``max(0, h - stride)``
        sessions of their future return. Treating them as independent inflates every t-statistic by
        roughly sqrt(h / stride), which is the most common way a forecasting result is oversold.

        Charges only for overlap *within* a name. Forecasts on different names in the same session
        are also dependent - they share the market factor, and on Indian equities that is most of
        their common variance - but discounting for that needs the cross-sectional correlation of the
        residuals, which :mod:`..evaluation.families` already estimates from an eigenvalue spectrum.
        So this number is an upper bound on the independent sample, not an estimate of it.
        """
        stride = self.params.get("stride") or self.horizon
        factor = min(1.0, stride / self.horizon)
        by_name: dict[str, int] = {}
        for fc, _ in self.records:
            by_name[fc.instrument] = by_name.get(fc.instrument, 0) + 1
        return sum(c * factor for c in by_name.values())

    def score(self) -> dict:
        """Calibration, sharpness and skill against each null, all on the same sample."""
        if not self.records:
            raise ForecastError("the study recorded nothing")
        candidate = C.score(self.records)
        out = {
            "model": self.model,
            "horizon": self.horizon,
            "n": self.n,
            "independent_observations": self.independent_observations,
            "instruments": len(self.instruments),
            "sessions": len(set(self.sessions)),
            "first": min(self.sessions) if self.sessions else None,
            "last": max(self.sessions) if self.sessions else None,
            "skipped": dict(self.skipped),
            "candidate": candidate,
            "directional": C.directional(self.records),
            "information_coefficient": self.information_coefficient(),
            "skill": {},
        }
        for name, recs in self.null_records.items():
            if len(recs) != len(self.records):
                out["skill"][name] = {
                    "why": f"the null was scored on {len(recs)} observations and the candidate on "
                           f"{len(self.records)}; not comparable"}
                continue
            out["skill"][name] = C.skill(candidate, C.score(recs))
        # A null run as its own candidate compares with itself, and that comparison is excluded
        # rather than counted as a failure.
        judged = [v for v in out["skill"].values() if not v.get("identical")]
        out["beats_every_null"] = bool(judged) and all(
            v.get("beats_null") is True for v in judged)
        return out

    def information_coefficient(self) -> dict:
        """Cross-sectional Spearman IC of forecast expected return against realised return.

        This is the statistic that makes a forecast model commensurable with every other signal in
        ``evaluation_runs``: a rank correlation, computed within a session across names, averaged
        across sessions. Sessions with fewer than five names are dropped, because a rank correlation
        over three observations takes four values.

        The t-statistic uses the number of **sessions**, not the number of forecasts. An IC series
        of 40 sessions has 40 observations however many names each one ranked.
        """
        by_session: dict[date, list[tuple[float, float]]] = {}
        for fc, observed in self.records:
            by_session.setdefault(fc.as_of, []).append(
                (fc.expected_return(), observed / fc.anchor - 1))
        ics, ic_dates = [], []
        for session in sorted(by_session):
            pairs = by_session[session]
            if len(pairs) < 5:
                continue
            ic = _spearman([a for a, _ in pairs], [b for _, b in pairs])
            if ic is not None:
                ics.append(ic)
                ic_dates.append(session)
        if len(ics) < 3:
            return {"mean_ic": None, "ic_t": None, "sessions": len(ics),
                    "why": f"{len(ics)} usable sessions; an IC series needs a cross-section of at "
                           f"least five names on at least three sessions"}
        m = sum(ics) / len(ics)
        var = sum((x - m) ** 2 for x in ics) / (len(ics) - 1)
        sd = math.sqrt(var)
        return {
            "mean_ic": m,
            "ic_t": (m / (sd / math.sqrt(len(ics)))) if sd > 0 else None,
            "sessions": len(ics),
            "ic_series": ics,
            "ic_dates": ic_dates,
            "why": "t on the number of sessions, not the number of forecasts",
        }


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """Rank correlation with average ranks for ties. None when either side is constant."""
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    n = len(rx)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return (num / (dx * dy)) if dx > 0 and dy > 0 else None


# ---------------------------------------------------------------------- the point-in-time reads
def calendar(con, start: date, end: date) -> list[date]:
    return [r[0] for r in con.execute(
        """SELECT DISTINCT business_date FROM adjusted_prices
           WHERE business_date BETWEEN ? AND ? ORDER BY 1""", [start, end]).fetchall()]


def history_for(con, isin: str, as_of: date, length: int = HISTORY) -> list[float]:
    """Closes up to and including ``as_of``, oldest first. Nothing after it, ever."""
    rows = con.execute(
        """SELECT close_adj FROM adjusted_prices
           WHERE isin = ? AND business_date <= ? AND close_adj > 0
           ORDER BY business_date DESC LIMIT ?""", [isin, as_of, length]).fetchall()
    return [r[0] for r in reversed(rows)]


def realised(con, isin: str, as_of: date, horizon: int) -> float | None:
    """The close exactly ``horizon`` trading sessions after ``as_of``, on the exchange calendar.

    Counted in sessions rather than days on purpose. A forecast scored 20 calendar days out is
    scored over a window whose length depends on where the holidays fell, and on the Indian calendar
    that ranges from 12 to 15 sessions.
    """
    rows = con.execute(
        """SELECT close_adj FROM adjusted_prices
           WHERE isin = ? AND business_date > ? AND close_adj > 0
           ORDER BY business_date LIMIT ?""", [isin, as_of, horizon]).fetchall()
    return rows[horizon - 1][0] if len(rows) == horizon else None


def sample_grid(con, *, start: date, end: date, horizon: int, stride: int | None = None,
                names: int = 50, min_adv: float = 1e7) -> list[tuple[date, str]]:
    """A strided grid of (session, instrument) pairs, chosen point-in-time on each session.

    The universe is re-read on every sample date from ``features.adv20`` as it stood then, so the
    grid is not a list of names that were liquid later.
    """
    stride = stride or horizon
    cal = calendar(con, start, end)
    out: list[tuple[date, str]] = []
    for session in cal[::stride]:
        rows = con.execute("""
            SELECT l.isin FROM features f
            JOIN security_lineage l ON l.lineage = f.lineage
            WHERE f.business_date = (SELECT MAX(business_date) FROM features
                                     WHERE business_date <= ?)
              AND f.adv20 >= ?
            ORDER BY f.adv20 DESC, l.isin
            LIMIT ?""", [session, min_adv, names]).fetchall()
        out.extend((session, r[0]) for r in rows)
    return out


# ------------------------------------------------------------------------------- the walk
def walk(con, forecaster, *, start: date, end: date, horizon: int,
         stride: int | None = None, names: int = 50, n_paths: int = 200,
         min_adv: float = 1e7, against_nulls: bool = True,
         seed: int = 0) -> Study:
    """Walk ``forecaster`` across a point-in-time grid, and every null across the same grid.

    The nulls run on *exactly* the pairs the candidate produced a forecast for. If the candidate
    skips a name for want of history and the null does not, the two are no longer scored on the same
    sample and a skill score between them is arithmetic on different data - so pairs are only kept
    when every forecaster in the comparison succeeded on them.
    """
    stride = stride or horizon
    grid = sample_grid(con, start=start, end=end, horizon=horizon, stride=stride,
                       names=names, min_adv=min_adv)
    model_name = getattr(forecaster, "name", forecaster.__class__.__name__)
    study = Study(model=model_name, horizon=horizon,
                  params={"stride": stride, "names": names, "n_paths": n_paths,
                          "min_adv": min_adv, "history": HISTORY, "seed": seed,
                          "start": str(start), "end": str(end)})
    refs = nulls(seed=seed) if against_nulls else {}
    for name in refs:
        study.null_records[name] = []

    for session, isin in grid:
        prices = history_for(con, isin, session)
        observed = realised(con, isin, session, horizon)
        if observed is None:
            study.skipped["no_realised_price"] = study.skipped.get("no_realised_price", 0) + 1
            continue
        if len(prices) < 2:
            study.skipped["no_history"] = study.skipped.get("no_history", 0) + 1
            continue

        made: dict[str, ForecastDistribution] = {}
        try:
            made[model_name] = forecaster.forecast(
                instrument=isin, as_of=session, prices=prices, horizon=horizon,
                n_paths=n_paths)
            for name, f in refs.items():
                made[name] = f.forecast(instrument=isin, as_of=session, prices=prices,
                                        horizon=horizon, n_paths=n_paths)
        except ForecastError:
            # Every forecaster or none: a pair kept for the candidate and dropped for a null makes
            # the skill score a comparison across different samples.
            study.skipped["forecaster_declined"] = (
                study.skipped.get("forecaster_declined", 0) + 1)
            continue

        study.records.append((made[model_name], observed))
        for name in refs:
            study.null_records[name].append((made[name], observed))
        study.sessions.append(session)
        study.instruments.add(isin)

    return study


def record(con, study: Study, scored: dict | None = None) -> dict:
    """Enter the study in ``evaluation_runs``, so it faces the bar every other trial faces.

    The feature name is ``forecast:<model>``, which puts it in its own research family and means the
    Bonferroni bar rises for everything else in the ledger the moment a forecast model is tried. That
    is the intended cost: trying a foundation model is a trial, and the arithmetic does not care how
    many parameters it has.
    """
    scored = scored or study.score()
    ic = scored["information_coefficient"]
    reasons = []
    if ic.get("mean_ic") is None:
        reasons.append(ic.get("why", "no IC series"))
    if not scored["beats_every_null"]:
        losers = [k for k, v in scored["skill"].items()
                  if not v.get("identical") and v.get("beats_null") is not True]
        reasons.append(f"does not beat {', '.join(losers)}")
    if scored["independent_observations"] < scored["n"]:
        reasons.append(
            f"{scored['n']} forecasts carry {scored['independent_observations']:.0f} independent "
            f"observations")

    con.execute("""
        INSERT INTO evaluation_runs
        (run_at, version, feature, horizon, params, dates, mean_ic, ic_t, sharpe,
         deflated_sharpe, verdict, reasons, ic_series, ic_dates)
        VALUES (CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?)
    """, [VERSION, f"forecast:{study.model}", study.horizon,
          json.dumps(study.params, sort_keys=True, default=str),
          ic.get("sessions") or 0, ic.get("mean_ic"), ic.get("ic_t"),
          "REJECT" if reasons else "CANDIDATE", json.dumps(reasons),
          ic.get("ic_series"), ic.get("ic_dates")])
    return {"feature": f"forecast:{study.model}", "horizon": study.horizon,
            "verdict": "REJECT" if reasons else "CANDIDATE", "reasons": reasons}
