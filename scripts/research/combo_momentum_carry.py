"""One combination, fixed before the run: frog-in-the-pan momentum (net excess over the 500) plus rich-basis
carry (net excess over the risk-free rate), inverse-volatility weights from the trailing 24 months (equal weight
until 24 months exist), monthly. Months with no rich basis hold only momentum's share; the carry share earns zero
excess. Family 2 (``combo_m1_carry`` was the first combination registered); prior B, the weaker member's.

    python scripts/research/combo_momentum_carry.py   -> docs/trials/dynamic/combo-momentum-carry.json
"""
import json
from pathlib import Path

import pandas as pd

from financial_brain.evaluation import dynamic_standard as D

OUT = Path("docs/trials/dynamic")


def main() -> None:
    mom = pd.read_parquet(OUT / "momentum-excess-series.parquet")["P3 frog in the pan"]
    car = pd.read_parquet("data/edges/c1_monthly.parquet")["net"]
    mom.index = pd.to_datetime(mom.index).to_period("M")
    car.index = pd.to_datetime(car.index).to_period("M")
    df = pd.DataFrame({"mom": mom, "car": car}).loc[car.index.min():].dropna(subset=["mom"])
    df["car"] = df["car"].fillna(0.0)
    vol = df.rolling(24, min_periods=24).std().shift(1)
    w = (1 / vol).div((1 / vol).sum(axis=1), axis=0).fillna(0.5)
    combo = (w * df).sum(axis=1)
    combo.index = combo.index.to_timestamp("M")
    g = D.judge(D.Spec("P3 momentum + C1 carry, inverse-vol", "B", 2), combo, null_pass=None)
    res = {"months": len(combo), "corr": float(df["mom"].corr(df["car"])), "mean_weight_mom": float(w["mom"].mean()),
           "dynamic": g}
    print(json.dumps(res, indent=1, default=str))
    (OUT / "combo-momentum-carry.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
