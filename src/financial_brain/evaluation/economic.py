"""Two experiments that decide whether any of this is worth running.

Everything else in ``evaluation/`` measures a signal. These two measure the *process*, and they are
the only questions whose answers can stop the project:

**Does the horizon choice survive being made without hindsight?** h60 was selected by sweeping seven
configurations over 2015-2026 and reading the best one, then reported on 2015-2026. That is a fit
until it is walked forward. :func:`walk_forward_selection` chooses the horizon on a training window
and spends it on the next one, rolling, so every number it reports was earned on data the choice had
not seen.

**Does it beat the trivial alternatives?** :func:`baselines` runs the naive versions through the same
engine, the same universe, the same costs and the same control. If a 102M-parameter model, a committee,
a knowledge graph and eleven years of point-in-time data cannot beat ranking on a twelve-month return,
the complexity is not earning its keep and should be removed rather than extended.

## What is deliberately not here

A naive **value** or **quality** baseline. ``features`` holds 34 columns and every one of them is
derived from price and volume - there are no point-in-time fundamentals in this archive, so a
price-to-book or return-on-equity baseline would have to be assembled from data that does not exist
at the right timestamps. Reporting one built from today's fundamentals applied to 2016 would make the
baselines easier to beat, which is the wrong direction to be wrong in. The gap is stated instead.

## The purge

Selecting on a training window and testing on the session immediately after it leaks the horizon: the
last rebalance inside training holds positions whose returns land in the test window. Every fold
therefore discards ``gap`` sessions between train and test, and ``gap`` is at least the longest
horizon under consideration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..paper import engine

#: Horizons the walk-forward is allowed to choose between. Kept small on purpose: every extra
#: candidate is another trial charged against the whole ledger, and a grid wide enough to guarantee a
#: winner in every fold guarantees nothing about the next one.
HORIZONS = (20, 60, 120)

#: Naive baselines, as (feature, direction, what the ranking means).
BASELINES = (
    ("mom_12_1", 1, "twelve-month return skipping the last month - Jegadeesh & Titman momentum"),
    ("ret_20d", -1, "worst one-month return first - short-horizon reversal"),
    ("vol_60", -1, "lowest sixty-session volatility first - the low-volatility anomaly"),
    ("dist_52w_high", 1, "closest to the 52-week high - the candidate under test"),
)

#: Index series to report alongside. Cap-weighted, so they are a different thing from the
#: equal-weight universe control and both are needed.
INDICES = ("Nifty 50", "Nifty 500")


@dataclass
class Fold:
    index: int
    train: tuple[date, date]
    test: tuple[date, date]
    gap_sessions: int
    chosen: int | None = None
    chosen_on: dict = field(default_factory=dict)      # train excess by horizon
    out_of_sample: dict = field(default_factory=dict)  # test excess by horizon

    def as_dict(self) -> dict:
        return {"fold": self.index,
                "train": [str(self.train[0]), str(self.train[1])],
                "test": [str(self.test[0]), str(self.test[1])],
                "gap_sessions": self.gap_sessions,
                "chosen": self.chosen,
                "chosen_on": self.chosen_on,
                "out_of_sample": self.out_of_sample,
                "oos_of_chosen": ((self.out_of_sample.get(self.chosen) or {}).get("excess")
                                  if self.chosen else None),
                "train_of_chosen": ((self.chosen_on.get(self.chosen) or {}).get("excess")
                                    if self.chosen else None)}


def _calendar(con, start: date, end: date) -> list[date]:
    return [r[0] for r in con.execute(
        """SELECT DISTINCT business_date FROM adjusted_prices
           WHERE business_date BETWEEN ? AND ? ORDER BY 1""", [start, end]).fetchall()]


def _excess(con, *, feature: str, direction: int, start: date, end: date,
            rebalance: int, max_positions: int, capital_inr: float,
            min_adv: float, charge_costs: bool = True) -> dict | None:
    """One run's excess over its own universe, or None when it cannot be run.

    Two refusals are caught, and both have to be reported rather than raised: a window too short for
    the horizon, and a feature this archive does not carry. A baseline comparison that aborts because
    one of its members is unavailable tells you nothing about the ones that were; a comparison that
    silently scores the missing one as zero is worse.
    """
    try:
        r = engine.run(con, feature=feature, start=start, end=end, direction=direction,
                       capital_inr=capital_inr, rebalance=rebalance,
                       max_positions=max_positions, min_adv=min_adv,
                       simulate_fills=False, charge_costs=charge_costs,
                       universe_label=f"{feature} h{rebalance} {start}..{end}")
    except engine.PaperError:
        return None
    except Exception as exc:                                       # noqa: BLE001
        # A missing feature column arrives as a database binder error, which is a statement about the
        # archive rather than a bug, so it is reported in the same shape as any other refusal.
        if "does not have a column" in str(exc):
            return None
        raise
    s = r.summary()
    return {"excess": s["excess_over_universe"], "total_return": s["total_return"],
            "universe_return": s["universe_return"], "sharpe": s["sharpe_annual"],
            "max_drawdown": s["max_drawdown"], "cost_drag": s["cost_drag"],
            "trades": s["trades"], "sessions": s["sessions"]}


def folds(con, *, start: date, end: date, initial_train: int = 1200,
          test_len: int = 400, gap: int | None = None,
          horizons=HORIZONS) -> list[Fold]:
    """Expanding-window folds with a purge gap, on the exchange calendar.

    Expanding rather than rolling: the choice of horizon is the kind of thing that should use all the
    history available at the time, and a rolling window would throw away the early years for no
    reason other than symmetry.
    """
    gap = gap or max(horizons)
    cal = _calendar(con, start, end)
    if len(cal) < initial_train + gap + test_len:
        raise ValueError(
            f"{len(cal)} sessions cannot carry a {initial_train}-session train, a {gap}-session "
            f"purge and a {test_len}-session test")
    out, i, train_end = [], 0, initial_train
    while train_end + gap + test_len <= len(cal):
        out.append(Fold(index=i,
                        train=(cal[0], cal[train_end - 1]),
                        test=(cal[train_end + gap], cal[train_end + gap + test_len - 1]),
                        gap_sessions=gap))
        i += 1
        train_end += test_len
    return out


def walk_forward_selection(con, *, feature: str = "dist_52w_high", direction: int = 1,
                           start: date, end: date, horizons=HORIZONS,
                           initial_train: int = 1200, test_len: int = 400,
                           max_positions: int = 100, capital_inr: float = 1e9,
                           min_adv: float = 1e7, progress=None) -> dict:
    """Choose the rebalance horizon on each training window and spend it on the next one.

    Returns the chosen horizon per fold, the out-of-sample excess of that choice, and - the number
    that matters - the same figure for every fixed horizon. If picking the horizon in advance and
    never changing it does as well, the selection added nothing and the sweep that produced h60 was
    measuring noise.
    """
    fs = folds(con, start=start, end=end, initial_train=initial_train,
               test_len=test_len, horizons=horizons)
    for f in fs:
        for h in horizons:
            tr = _excess(con, feature=feature, direction=direction, start=f.train[0],
                         end=f.train[1], rebalance=h, max_positions=max_positions,
                         capital_inr=capital_inr, min_adv=min_adv)
            te = _excess(con, feature=feature, direction=direction, start=f.test[0],
                         end=f.test[1], rebalance=h, max_positions=max_positions,
                         capital_inr=capital_inr, min_adv=min_adv)
            if tr:
                f.chosen_on[h] = tr
            if te:
                f.out_of_sample[h] = te
            if progress:
                progress(f, h, tr, te)
        if f.chosen_on:
            f.chosen = max(f.chosen_on, key=lambda h: f.chosen_on[h]["excess"])

    picked = [f.out_of_sample[f.chosen]["excess"] for f in fs
              if f.chosen and f.chosen in f.out_of_sample]
    fixed = {h: [f.out_of_sample[h]["excess"] for f in fs if h in f.out_of_sample]
             for h in horizons}

    def mean(xs):
        return sum(xs) / len(xs) if xs else None

    best_fixed = max((h for h in horizons if fixed[h]),
                     key=lambda h: mean(fixed[h]) or float("-inf"), default=None)
    return {
        "feature": feature,
        "folds": [f.as_dict() for f in fs],
        "n_folds": len(fs),
        "chosen_per_fold": [f.chosen for f in fs],
        "changed_its_mind": len({f.chosen for f in fs if f.chosen}) > 1,
        "oos_excess_of_selection": picked,
        "mean_oos_excess_of_selection": mean(picked),
        "oos_excess_by_fixed_horizon": {h: mean(v) for h, v in fixed.items()},
        "best_fixed_horizon": best_fixed,
        "selection_beat_best_fixed": (
            (mean(picked) or float("-inf")) > (mean(fixed[best_fixed]) or float("-inf"))
            if best_fixed else None),
        "folds_where_selection_was_positive": sum(1 for x in picked if x > 0),
        "why": "the horizon is chosen on the training window only, with a purge gap, so the "
               "out-of-sample column was never used to make the choice. If a fixed horizon does as "
               "well, the selection was fitting noise.",
    }


def baselines(con, *, start: date, end: date, rebalance: int = 60,
              max_positions: int = 100, capital_inr: float = 1e9,
              min_adv: float = 1e7, which=BASELINES,
              indices=INDICES, progress=None) -> dict:
    """The naive alternatives, through the same engine, universe, costs and control.

    The calibration floor is computed at **this** ``rebalance``, not at some canonical one. The floor
    is the engine's own drift and it grows sharply as the horizon lengthens - fewer rebalances means
    less trimming, so weights drift further inside the no-trade band and the whole-universe book picks
    up more of a momentum tilt the cost-free control never gets. Measured on this database: +34.26% at
    h20 and **+130.79% at h60**. An h60 excess judged against the h20 floor is being judged against a
    different instrument, and looks about a hundred points better than it is.
    """
    out = {}
    for feature, direction, what in which:
        row = _excess(con, feature=feature, direction=direction, start=start, end=end,
                      rebalance=rebalance, max_positions=max_positions,
                      capital_inr=capital_inr, min_adv=min_adv)
        gross = _excess(con, feature=feature, direction=direction, start=start, end=end,
                        rebalance=rebalance, max_positions=max_positions,
                        capital_inr=capital_inr, min_adv=min_adv, charge_costs=False)
        if row:
            row["gross_excess"] = gross["excess"] if gross else None
            row["what"] = what
            row["direction"] = direction
        out[feature] = row
        if progress:
            progress(feature, row)

    levels = {}
    for name in indices:
        r = con.execute("""
            SELECT (SELECT close_level FROM index_levels WHERE index_name = ?
                      AND business_date <= ? AND close_level > 0
                    ORDER BY business_date DESC LIMIT 1),
                   (SELECT close_level FROM index_levels WHERE index_name = ?
                      AND business_date <= ? AND close_level > 0
                    ORDER BY business_date DESC LIMIT 1)
        """, [name, start, name, end]).fetchone()
        levels[name] = (r[1] / r[0] - 1) if r and r[0] and r[1] else None

    # The floor can itself be unmeasurable on a short window, and that is a reportable state: without
    # it there is no way to know whether a ranking is signal or the engine's own drift, so the
    # comparison is returned with the floor as None rather than with a number nobody measured.
    try:
        floor = engine.calibrate(con, start=start, end=end, min_adv=min_adv,
                                 rebalance=rebalance, capital_inr=capital_inr)
    except engine.PaperError as exc:
        floor = {"floor": None, "universe_return": None, "why": str(exc)}

    ranked = sorted((k for k, v in out.items() if v),
                    key=lambda k: out[k]["excess"], reverse=True)
    f = floor["floor"]
    return {
        "strategies": out,
        "indices": levels,
        "floor": f,
        "floor_unavailable": floor.get("why") if f is None else None,
        "universe_return": floor["universe_return"],
        "ranking": ranked,
        "inside_the_floor": ([k for k in ranked if abs(out[k]["excess"]) <= abs(f)]
                             if f is not None else []),
        "missing": ["naive value", "naive quality", "sector-neutral momentum"],
        "why_missing": "features holds 34 columns and all of them derive from price and volume; "
                       "there are no point-in-time fundamentals or sector labels in this archive, "
                       "and building those baselines from present-day data would make them easier "
                       "to beat",
        "rebalance": rebalance,
        "window": [str(start), str(end)],
    }
