"""A System One model for timing: act, or wait? (ADR-0002 applied to our own data)

The router asks a language model "which of these labels, and how sure?" and only trusts
confidences that were measured calibrated. This asks the same question of the market,
with a model fitted on our own history rather than rented:

    decide(features on date t) -> Decision(label="act" | "wait", confidence)

What it predicts is deliberately modest: whether a name beats the cross-section over the
next 20 sessions. Not a price, not a return - a **rank**, which is the only thing a
cross-sectional signal can honestly claim, and the same target the firewall tests.

The discipline is the router's, not a notebook's:

* **Cross-sectional ranks, not raw values.** Every feature is ranked within its session,
  so the model cannot learn "2020 was a good year" and call it skill.
* **A strict time split.** Fit on the earliest years, calibrate on the middle, and report
  only what the untouched final years show. Nothing from the test period touches the
  weights or the threshold.
* **A calibrated acceptance threshold**, chosen the way the model router chooses one: the
  lowest confidence at which "act" was right at least ``target`` of the time, with a
  minimum number of cases behind it.
* **The right to decline.** If no threshold reaches the target out of sample, the model
  says so and ``decide`` refuses to act at all. A timing model that cannot beat its own
  base rate is worse than none, because it feels like information.

Logistic regression, fitted with plain gradient descent on numpy. The point here is not
model class - a bigger model on the same features would flatter itself on the same
history - it is that the threshold is measured on data the fit never saw.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

FEATURES = ("ret_20d", "ret_60d", "mom_12_1", "vol_60", "dist_52w_high",
            "news_20d", "dealing_60d", "adverse_60d")
HORIZON = 20                 # sessions
MIN_SUPPORT = 200            # accepted cases needed before a threshold means anything
TARGET = 0.55                # "act" must be right this often; a coin is 0.50
VERSION = "t1"


@dataclass
class Model:
    weights: list[float]
    bias: float
    features: tuple[str, ...] = FEATURES
    threshold: float | None = None
    fitted_on: tuple[str, str] = ("", "")
    verified_on: tuple[str, str] = ("", "")
    report: dict = field(default_factory=dict)

    def usable(self) -> bool:
        return self.threshold is not None

    def probability(self, row: list[float]) -> float:
        import math
        z = self.bias + sum(w * x for w, x in zip(self.weights, row, strict=True))
        return 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))


def panel(con, start: date | str, end: date | str, *, min_adv: float = 1e7):
    """Cross-sectionally ranked features and the forward outcome, every 20th session.

    The label is "did this name beat the **equal-weighted mean** of its own session over
    the next 20 sessions" - the return of the universe you could actually hold.

    It was the median at first, and that was wrong in a way worth recording: the Indian
    cross-section is strongly right-skewed, so the mean sits about 1.2 percentage points
    above the median over 20 sessions. Beating the median is easy and unbuyable. Scored
    against it, this model showed +0.94% out of sample; scored against the mean, the same
    accepted cases gave -0.29%. The entire apparent edge was the benchmark.
    """
    import numpy as np
    cols = ", ".join(f"f.{c}" for c in FEATURES)
    ranks = ", ".join(
        f"PERCENT_RANK() OVER (PARTITION BY business_date ORDER BY {c}) AS r_{c}"
        for c in FEATURES)
    rows = con.execute(f"""
        WITH cal AS (
            SELECT business_date, ROW_NUMBER() OVER (ORDER BY business_date) AS k
            FROM (SELECT DISTINCT business_date FROM adjusted_prices)
        ), base AS (
            SELECT f.business_date, f.lineage, {cols}, c.k,
                   a.close_adj AS px,
                   (SELECT b.close_adj FROM adjusted_prices b JOIN cal c2
                      ON c2.business_date = b.business_date
                     WHERE b.lineage = f.lineage AND c2.k = c.k + {HORIZON}) AS px_fwd
            FROM features f
            JOIN cal c USING (business_date)
            JOIN adjusted_prices a ON a.lineage = f.lineage
                                  AND a.business_date = f.business_date
            WHERE (c.k - 1) % {HORIZON} = 0 AND f.adv20 >= ?
              AND f.business_date >= CAST(? AS DATE) AND f.business_date <= CAST(? AS DATE)
              AND {" AND ".join(f"f.{c} IS NOT NULL" for c in FEATURES)}
        ), fwd AS (
            SELECT *, px_fwd / px - 1 AS y FROM base WHERE px_fwd IS NOT NULL AND px > 0
        ), ranked AS (
            SELECT business_date, lineage, y, {ranks},
                   AVG(y) OVER (PARTITION BY business_date) AS y_med
            FROM fwd
        )
        SELECT business_date, {", ".join("r_" + c for c in FEATURES)},
               CASE WHEN y > y_med THEN 1 ELSE 0 END AS label,
               y - y_med AS excess
        FROM ranked ORDER BY business_date
        """, [min_adv, str(start), str(end)]).fetchall()
    if not rows:
        return np.zeros((0, len(FEATURES))), np.zeros(0), np.zeros(0)
    x = np.array([[r[i + 1] for i in range(len(FEATURES))] for r in rows], dtype=float)
    y = np.array([r[-2] for r in rows], dtype=float)
    # Rank accuracy is not money: keep the size of the win as well as its sign.
    excess = np.array([r[-1] for r in rows], dtype=float)
    return x, y, excess


def fit(x, y, *, steps: int = 400, lr: float = 0.5, l2: float = 1e-3) -> tuple:
    """Logistic regression by gradient descent. Features are already in [0, 1]."""
    import numpy as np
    n, d = x.shape
    w, b = np.zeros(d), 0.0
    for _ in range(steps):
        z = x @ w + b
        p = 1 / (1 + np.exp(-np.clip(z, -30, 30)))
        err = p - y
        w -= lr * ((x.T @ err) / n + l2 * w)
        b -= lr * err.mean()
    return w.tolist(), float(b)


def calibrate(probs, y, *, target: float = TARGET, min_support: int = MIN_SUPPORT):
    """The lowest confidence at which "act" was right at least ``target`` of the time.

    Exactly the rule the model router uses on language models, applied to this one.
    """
    import numpy as np
    order = np.argsort(-probs)
    p_sorted, y_sorted = probs[order], y[order]
    best = None
    for i in range(min_support, len(p_sorted) + 1):
        hit = y_sorted[:i].mean()
        if hit >= target:
            best = float(p_sorted[i - 1])
    return best


def build(con, *, fit_end: str = "2022-12-31", calib_end: str = "2024-06-30",
          test_end: str = "2026-09-18", target: float = TARGET) -> Model:
    """Fit, calibrate and verify - each on its own slice of time, in order."""
    import numpy as np
    x_fit, y_fit, _ = panel(con, "2015-01-01", fit_end)
    x_cal, y_cal, _ = panel(con, fit_end, calib_end)
    x_test, y_test, ex_test = panel(con, calib_end, test_end)
    if min(len(y_fit), len(y_cal), len(y_test)) < MIN_SUPPORT:
        return Model(weights=[0.0] * len(FEATURES), bias=0.0,
                     report={"error": "not enough rows in one of the slices",
                             "rows": [len(y_fit), len(y_cal), len(y_test)]})

    w, b = fit(x_fit, y_fit)
    model = Model(weights=w, bias=b, fitted_on=("2015-01-01", fit_end),
                  verified_on=(calib_end, test_end))

    p_cal = 1 / (1 + np.exp(-np.clip(x_cal @ np.array(w) + b, -30, 30)))
    threshold = calibrate(p_cal, y_cal, target=target)

    p_test = 1 / (1 + np.exp(-np.clip(x_test @ np.array(w) + b, -30, 30)))
    base_rate = float(y_test.mean())
    report = {"rows": {"fit": len(y_fit), "calibrate": len(y_cal), "test": len(y_test)},
              "base_rate_test": round(base_rate, 4), "target": target,
              "threshold_from_calibration": threshold}
    if threshold is None:
        report["verdict"] = "no threshold reached the target even in calibration"
        model.report = report
        return model

    taken = p_test >= threshold
    report["coverage_test"] = round(float(taken.mean()), 4)
    report["n_taken_test"] = int(taken.sum())
    if taken.sum() >= MIN_SUPPORT:
        precision = float(y_test[taken].mean())
        report["precision_test"] = round(precision, 4)
        report["lift_over_base"] = round(precision - base_rate, 4)
        # The question that decides whether any of this is worth trading: what did the
        # accepted cases actually earn over the session median, and does it survive a
        # round trip? A 64% rank hit rate on wins of 0.1% is not an edge.
        from ..costs.india import CostModel
        cost = CostModel().round_trip(turnover=1_000_000, bucket="mid")["bps"] / 10_000
        gross = float(ex_test[taken].mean())
        report["mean_excess_test"] = round(gross, 5)
        report["cost_round_trip"] = round(cost, 5)
        report["net_excess_test"] = round(gross - cost, 5)
        report["mean_excess_all"] = round(float(ex_test.mean()), 5)
        if precision >= target and gross - cost > 0:
            model.threshold = threshold
            report["verdict"] = (f"usable out of sample: {precision:.1%} precision, "
                                 f"{gross - cost:+.2%} net of costs per 20 sessions")
        elif precision >= target:
            report["verdict"] = (f"beat the base rate ({precision:.1%}) but only "
                                 f"{gross:+.2%} gross, which a {cost:.2%} round trip "
                                 f"erases - declined")
        else:
            report["verdict"] = (f"held {precision:.1%} out of sample against a "
                                 f"{target:.0%} bar - declined")
    else:
        report["verdict"] = (f"only {int(taken.sum())} accepted cases out of sample "
                             f"(needs {MIN_SUPPORT}) - declined")
    model.report = report
    return model


def save(con, model: Model) -> None:
    con.execute("""INSERT INTO timing_models (version, fitted_at, weights, bias, features,
                   threshold, fitted_on, verified_on, report)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                [VERSION, datetime.now(timezone.utc), json.dumps(model.weights),
                 model.bias, json.dumps(list(model.features)), model.threshold,
                 json.dumps(list(model.fitted_on)), json.dumps(list(model.verified_on)),
                 json.dumps(model.report)])


def latest(con) -> Model | None:
    row = con.execute("""SELECT weights, bias, features, threshold, fitted_on,
                         verified_on, report FROM timing_models
                         ORDER BY fitted_at DESC LIMIT 1""").fetchone()
    if not row:
        return None
    return Model(weights=json.loads(row[0]), bias=row[1],
                 features=tuple(json.loads(row[2])), threshold=row[3],
                 fitted_on=tuple(json.loads(row[4])), verified_on=tuple(json.loads(row[5])),
                 report=json.loads(row[6]))


def decide(con, isin: str, as_of: date, *, model: Model | None = None):
    """A typed decision for one name on one date - or a refusal.

    Returns the same ``Decision`` shape the router uses, so a caller cannot tell a local
    regression from a language model, and neither is trusted without its threshold.
    """
    from ..llm.system1 import Decision
    model = model or latest(con)
    if model is None or not model.usable():
        return Decision(label="wait", probs={}, confidence=0.0, model=f"timing-{VERSION}",
                        extra={"declined": "no verified timing model"})
    row = con.execute(f"""
        WITH ranked AS (
            SELECT l.isin, {", ".join(
                f"PERCENT_RANK() OVER (ORDER BY f.{c}) AS r_{c}" for c in model.features)}
            FROM features f JOIN security_lineage l ON l.lineage = f.lineage
            WHERE f.business_date = (SELECT MAX(business_date) FROM features
                                     WHERE business_date <= ?)
              AND {" AND ".join(f"f.{c} IS NOT NULL" for c in model.features)}
        ) SELECT * EXCLUDE (isin) FROM ranked WHERE isin = ?""",
                      [as_of, isin]).fetchone()
    if not row:
        return Decision(label="wait", probs={}, confidence=0.0, model=f"timing-{VERSION}",
                        extra={"declined": "no features for this name on that date"})
    p = model.probability(list(row))
    act = p >= model.threshold
    return Decision(label="act" if act else "wait", probs={"act": p, "wait": 1 - p},
                    confidence=p if act else 1 - p, model=f"timing-{VERSION}",
                    extra={"threshold": model.threshold})


def walk_forward(con, folds=(("2020-12-31", "2021-12-31", "2022-12-31"),
                             ("2021-12-31", "2022-12-31", "2023-12-31"),
                             ("2022-12-31", "2023-12-31", "2024-12-31"),
                             ("2023-12-31", "2024-12-31", "2025-12-31"),
                             ("2024-12-31", "2025-06-30", "2026-09-18")),
                 *, target: float = TARGET) -> dict:
    """The test that decides whether one good split was luck.

    Each fold fits on everything up to its first date, calibrates on the second, and
    reports only the third - so the same model-building recipe is judged on five
    different, non-overlapping futures. A recipe that earns in one and loses in three is
    a recipe that found a period, not an effect.
    """
    results = []
    for fit_end, calib_end, test_end in folds:
        m = build(con, fit_end=fit_end, calib_end=calib_end, test_end=test_end,
                  target=target)
        r = m.report
        results.append({"fit_end": fit_end, "test": f"{calib_end}..{test_end}",
                        "n_taken": r.get("n_taken_test"),
                        "precision": r.get("precision_test"),
                        "base_rate": r.get("base_rate_test"),
                        "net_excess": r.get("net_excess_test"),
                        "usable": m.usable(), "verdict": r.get("verdict")})
    tradeable = [r for r in results if r["net_excess"] is not None]
    positive = [r for r in tradeable if r["net_excess"] > 0]
    return {"folds": results, "folds_tested": len(tradeable),
            "folds_positive_net": len(positive),
            "mean_net_excess": (round(sum(r["net_excess"] for r in tradeable)
                                      / len(tradeable), 5) if tradeable else None),
            "holds": len(tradeable) >= 4 and len(positive) >= len(tradeable) - 1}


def score_all(con, *, first_year: int = 2019, min_adv: float = 1e7) -> dict:
    """Write ``timing_score`` into ``features``, fitted point in time.

    Each year is scored by weights fitted only on data that ended before it - an
    expanding window - so the column can be tested by the firewall like any other
    signal without the score having seen its own future. Rows before the first scorable
    year stay NULL, because a signal that could not have existed must not be tested.
    """
    import numpy as np
    last = con.execute("SELECT MAX(business_date) FROM features").fetchone()[0]
    years = list(range(first_year, last.year + 1))
    ranks = ", ".join(
        f"PERCENT_RANK() OVER (PARTITION BY business_date ORDER BY {c}) AS r_{c}"
        for c in FEATURES)
    con.execute("""CREATE OR REPLACE TEMP TABLE _scores
                   (lineage VARCHAR, business_date DATE, score DOUBLE)""")
    fitted = {}
    for year in years:
        x_fit, y_fit, _ = panel(con, "2015-01-01", f"{year - 1}-12-31", min_adv=min_adv)
        if len(y_fit) < MIN_SUPPORT:
            continue
        w, b = fit(x_fit, y_fit)
        fitted[year] = (w, b)
        rows = con.execute(f"""
            WITH ranked AS (
                SELECT lineage, business_date, {ranks}
                FROM features
                WHERE EXTRACT(year FROM business_date) = ? AND adv20 >= ?
                  AND {" AND ".join(f"{c} IS NOT NULL" for c in FEATURES)}
            ) SELECT * FROM ranked""", [year, min_adv]).fetchall()
        if not rows:
            continue
        x = np.array([[r[i + 2] for i in range(len(FEATURES))] for r in rows], dtype=float)
        p = 1 / (1 + np.exp(-np.clip(x @ np.array(w) + b, -30, 30)))
        con.executemany("INSERT INTO _scores VALUES (?,?,?)",
                        [[rows[i][0], rows[i][1], float(p[i])] for i in range(len(rows))])

    con.execute("""CREATE OR REPLACE TABLE features AS
                   SELECT f.* EXCLUDE (timing_score), s.score AS timing_score
                   FROM (SELECT *, CAST(NULL AS DOUBLE) AS timing_score FROM features) f
                   LEFT JOIN _scores s USING (lineage, business_date)""")
    n = con.execute("SELECT COUNT(timing_score) FROM features").fetchone()[0]
    return {"years_fitted": sorted(fitted), "rows_scored": n}
