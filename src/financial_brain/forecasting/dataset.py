"""Exporting Indian equity bars for fine-tuning, and the split that makes the result mean anything.

The fine-tuning code is Kronos'. What is ours, and what decides whether a fine-tuned model's numbers
are worth reading, is the dataset: which bars, adjusted how, keyed on what, and split where.

## The split is the whole thing

A random train/test split of a price series leaks the future into training, and it is the most common
error in machine learning applied to markets. It is not subtle and it is not small: the model sees
2026 while being evaluated on 2024.

Splitting by time fixes that and leaves two subtler leaks, both handled here:

**Cross-sectional leakage.** Splitting *per name* - name A's history into train, name B's into test -
leaks just as badly, because Indian equities share most of their variance with the market factor. A
model trained on Reliance through 2026 has seen the 2026 market, and is then "tested" on Infosys in
2026. So the split is one global date boundary applied to every name at once.

**Horizon overlap at the boundary.** A training window ending the session before the boundary predicts
``predict_window`` sessions that lie *inside* validation. Without a gap the last training targets are
the first validation inputs. :data:`EMBARGO_MULTIPLE` sets the gap as a multiple of the prediction
window - the purged split of López de Prado (2018), and the multiple is above one because volatility
clustering makes adjacent windows dependent beyond their literal overlap.

**The out-of-sample window is declared and then not touched.** :func:`splits` returns it, and
:func:`export` refuses to write it unless asked explicitly. A holdout that gets looked at during
development is a validation set with a misleading name.

## What the bars are

Point-in-time adjusted NSE EQ candles from :mod:`.bars`, keyed on lineage. Both matter: an unadjusted
series reads a 1:10 split as a 90% crash, and an ISIN-keyed series stops at the first succession -
12.84% of lineages change ISIN inside 2015-2026, and those are precisely the names with corporate
events.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import bars as barmod
from .distribution import ForecastError

#: Gap between splits, as a multiple of the prediction window. One would remove only the literal
#: overlap; volatility clustering makes adjacent windows dependent past that, so the gap is wider.
EMBARGO_MULTIPLE = 2

#: Columns Kronos' CSV pipeline expects, in its order.
COLUMNS = ("timestamps", "open", "high", "low", "close", "volume", "amount")

#: Minimum bars a lineage needs to contribute a single training window.
MIN_BARS = 600


@dataclass(frozen=True)
class Split:
    name: str
    start: date
    end: date

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end


def splits(*, start: date, end: date, train_frac: float = 0.65,
           val_frac: float = 0.15, predict_window: int = 20,
           embargo_multiple: int = EMBARGO_MULTIPLE,
           sessions: list[date] | None = None) -> dict:
    """Three chronological splits with purge gaps between them.

    Fractions are of *sessions*, not of calendar span, so a split boundary does not drift with the
    holiday calendar. ``sessions`` is the exchange calendar; without it the fractions are applied to
    the calendar span and the result is approximate, which :func:`export` refuses to use.
    """
    if not 0 < train_frac < 1 or not 0 < val_frac < 1 or train_frac + val_frac >= 1:
        raise ForecastError(
            f"train {train_frac} + val {val_frac} leaves no out-of-sample window")
    if sessions is None:
        raise ForecastError(
            "the exchange calendar is required: applying fractions to a calendar span puts the "
            "boundary on a different session depending on where the holidays fell")
    cal = [d for d in sessions if start <= d <= end]
    gap = predict_window * embargo_multiple
    if len(cal) < gap * 4:
        raise ForecastError(f"{len(cal)} sessions cannot carry two {gap}-session gaps")

    n = len(cal)
    train_end = int(n * train_frac)
    val_start = train_end + gap
    val_end = val_start + int(n * val_frac)
    oos_start = val_end + gap
    if oos_start >= n - 1:
        raise ForecastError("the gaps consumed the out-of-sample window")

    return {
        "train": Split("train", cal[0], cal[train_end - 1]),
        "validation": Split("validation", cal[val_start], cal[val_end - 1]),
        "out_of_sample": Split("out_of_sample", cal[oos_start], cal[-1]),
        "gap_sessions": gap,
        "purged": [(cal[train_end], cal[val_start - 1]),
                   (cal[val_end], cal[oos_start - 1])],
        "sessions": n,
        "why": f"chronological, one boundary for every name at once, with {gap}-session purge gaps. "
               f"A per-name split leaks the market factor; no gap leaks the horizon.",
    }


def universe(con, *, as_of: date, names: int = 200, min_adv: float = 1e7) -> list[str]:
    """Lineages liquid enough to train on, chosen as of the *start* of the training window.

    As of the start, deliberately. Choosing the universe as of today selects names that survived and
    grew, and a model fine-tuned on that universe has been taught what winners look like.
    """
    return [r[0] for r in con.execute("""
        SELECT f.lineage FROM features f
        WHERE f.business_date = (SELECT MAX(business_date) FROM features
                                 WHERE business_date <= ?)
          AND f.adv20 >= ?
        ORDER BY f.adv20 DESC, f.lineage
        LIMIT ?""", [as_of, min_adv, names]).fetchall()]


def export(con, out_dir: str | Path, *, split: Split, lineages: list[str],
           include_out_of_sample: bool = False, min_bars: int = MIN_BARS) -> dict:
    """Write one CSV per lineage for a split, in Kronos' column format.

    Refuses to write the out-of-sample split unless ``include_out_of_sample`` is set. A holdout that
    gets exported by default gets looked at, and a holdout that gets looked at is a validation set.
    """
    if split.name == "out_of_sample" and not include_out_of_sample:
        raise ForecastError(
            "refusing to export the out-of-sample split. Pass include_out_of_sample=True only when "
            "the model is finished and the number is the last one you will take")
    out = Path(out_dir) / split.name
    out.mkdir(parents=True, exist_ok=True)

    written, skipped, rows_total = [], {}, 0
    for lineage in lineages:
        window = [b for b in barmod.history(con, lineage, split.end, length=100_000)
                  if b.session >= split.start]
        if len(window) < min_bars:
            skipped[lineage] = f"{len(window)} bars"
            continue
        path = out / f"{lineage}.csv"
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(COLUMNS)
            for b in window:
                w.writerow([b.session.isoformat(), f"{b.open:.4f}", f"{b.high:.4f}",
                            f"{b.low:.4f}", f"{b.close:.4f}", f"{b.volume:.0f}",
                            f"{b.turnover:.0f}"])
        written.append(str(path))
        rows_total += len(window)

    manifest = {
        "split": split.name,
        "start": str(split.start),
        "end": str(split.end),
        "lineages_written": len(written),
        "lineages_skipped": len(skipped),
        "skipped": skipped,
        "rows": rows_total,
        "columns": list(COLUMNS),
        "adjusted": True,
        "keyed_on": "lineage",
        "exchange_series": f"{barmod.EXCHANGE} {barmod.SERIES}",
        "note": "adjusted NSE EQ closes; every price field carries the same factor as close_adj and "
                "volume its reciprocal. Keyed on lineage because 12.84% of lineages change ISIN "
                "inside 2015-2026 and an ISIN-keyed series stops at the first succession.",
    }
    with open(out / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    return manifest


def audit(con, plan: dict, lineages: list[str]) -> dict:
    """Check a split plan for the leaks it is meant to prevent, before anything is trained.

    Cheap, and worth running every time: a split that looks right in a diagram and wrong in the data
    produces a model whose numbers cannot be interpreted and whose training cannot be undone.
    """
    tr, va, oo = plan["train"], plan["validation"], plan["out_of_sample"]
    problems = []
    if not tr.end < va.start:
        problems.append("train ends after validation starts")
    if not va.end < oo.start:
        problems.append("validation ends after the out-of-sample window starts")

    cal = [r[0] for r in con.execute(
        "SELECT DISTINCT business_date FROM adjusted_prices ORDER BY 1").fetchall()]
    for lo, hi in plan["purged"]:
        held = len([d for d in cal if lo <= d <= hi])
        if held < plan["gap_sessions"]:
            problems.append(f"purge gap {lo}..{hi} holds {held} sessions, "
                            f"not {plan['gap_sessions']}")

    counts = {}
    for name, sp in (("train", tr), ("validation", va), ("out_of_sample", oo)):
        counts[name] = con.execute("""
            SELECT COUNT(*) FROM adjusted_prices
            WHERE lineage IN (SELECT UNNEST(?)) AND business_date BETWEEN ? AND ?""",
            [lineages, sp.start, sp.end]).fetchone()[0]

    return {"clean": not problems, "problems": problems, "rows": counts,
            "gap_sessions": plan["gap_sessions"],
            "why": "one chronological boundary for every name, purge gaps wide enough that a "
                   "training window's targets cannot be a validation window's inputs"}
