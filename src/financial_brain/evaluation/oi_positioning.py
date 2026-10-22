"""Training-only Nifty option open-interest positioning study.

NSE F&O bhavcopy is an after-close observation. It can first inform the *next*
session's open. This module asks whether prior-window put/call OI extremes
condition next-session and five-session Nifty price-index returns. Index levels
are not executable fills; no trial verdict, paper order or live alert is issued.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

TRAIN_END = date(2021, 12, 31)
START = date(2016, 1, 1)


def index_reference(con: duckdb.DuckDBPyConnection, *, start: date = START,
                    end: date = TRAIN_END) -> pd.DataFrame:
    if end > TRAIN_END or start > end:
        raise ValueError("OI exploration is sealed to the 2016-2021 training period")
    return con.execute("""SELECT business_date AS trade_date, open_level AS open,
                                close_level AS close
        FROM index_levels_canonical
        WHERE index_name = 'Nifty 50' AND variant = 'PRICE'
          AND business_date BETWEEN ?::DATE - INTERVAL 400 DAY AND ?::DATE
          AND open_level > 0 AND close_level > 0 ORDER BY business_date""",
                       [start, end]).df()


def option_oi(con: duckdb.DuckDBPyConnection, reference: pd.DataFrame, *,
              end: date = TRAIN_END) -> pd.DataFrame:
    """Same-date CE/PE OI in a ±2% moneyness band, 7–35 calendar days to expiry."""
    if end > TRAIN_END or reference.empty or pd.to_datetime(reference["trade_date"]).max().date() > end:
        raise ValueError("reference must be nonempty and contain training dates only")
    con.register("_nifty_reference", reference)
    try:
        first = pd.to_datetime(reference["trade_date"]).min().date()
        return con.execute("""SELECT o.trade_date,
            SUM(CASE WHEN o.option_type = 'PE' THEN o.oi ELSE 0 END) AS put_oi,
            SUM(CASE WHEN o.option_type = 'CE' THEN o.oi ELSE 0 END) AS call_oi,
            COUNT(*) AS contracts_observed
            FROM fo_bhav o JOIN _nifty_reference r USING (trade_date)
            WHERE o.instrument = 'OPTIDX' AND o.symbol = 'NIFTY'
              AND o.trade_date BETWEEN ?::DATE AND ?::DATE
              AND o.option_type IN ('CE', 'PE') AND o.oi > 0 AND o.contracts > 0
              AND o.expiry BETWEEN o.trade_date + INTERVAL 7 DAY
                               AND o.trade_date + INTERVAL 35 DAY
              AND o.strike BETWEEN r.close * 0.98 AND r.close * 1.02
            GROUP BY o.trade_date ORDER BY o.trade_date""", [first, end]).df()
    finally:
        con.unregister("_nifty_reference")


def observations(reference: pd.DataFrame, oi: pd.DataFrame, *,
                 start: date = START, end: date = TRAIN_END,
                 min_history: int = 60) -> pd.DataFrame:
    """Prior-window thresholds, next-open entry and no post-cutoff outcomes."""
    if end > TRAIN_END or start > end or not 20 <= min_history <= 252:
        raise ValueError("invalid training window")
    required = {"trade_date", "open", "close"}
    if not required.issubset(reference):
        raise ValueError("index reference requires date, open and close")
    r = reference.copy().sort_values("trade_date")
    r["trade_date"] = pd.to_datetime(r["trade_date"])
    if r["trade_date"].duplicated().any() or r["trade_date"].max().date() > end:
        raise ValueError("reference contains duplicate or post-training dates")
    o = oi.copy()
    o["trade_date"] = pd.to_datetime(o["trade_date"])
    if o["trade_date"].duplicated().any() or (not o.empty and o["trade_date"].max().date() > end):
        raise ValueError("OI contains duplicate or post-training dates")
    x = r.merge(o, on="trade_date", how="left").sort_values("trade_date")
    x["pcr"] = (x["put_oi"] / x["call_oi"]).where((x["put_oi"] > 0) & (x["call_oi"] > 0))
    prior = x["pcr"].rolling(252, min_periods=min_history)
    x["prior_low"] = prior.quantile(0.2).shift(1)
    x["prior_high"] = prior.quantile(0.8).shift(1)
    x["condition"] = np.select([x["pcr"] <= x["prior_low"], x["pcr"] >= x["prior_high"]],
                               ["low_put_call_oi", "high_put_call_oi"], default="middle_or_unavailable")
    x["next_open"] = x["open"].shift(-1)
    x["next_close"] = x["close"].shift(-1)
    x["fifth_close"] = x["close"].shift(-5)
    x["next_session_return"] = x["next_close"] / x["next_open"] - 1
    x["five_session_return"] = x["fifth_close"] / x["next_open"] - 1
    x = x[(x["trade_date"] >= pd.Timestamp(start)) & (x["trade_date"] <= pd.Timestamp(end))]
    return x.dropna(subset=["pcr", "prior_low", "prior_high", "next_session_return",
                            "five_session_return"])


def diagnostic(x: pd.DataFrame) -> list[dict]:
    """Descriptive conditional-vs-unconditional contrasts, not independent-sample t tests."""
    if x.empty:
        return []
    out = []
    for condition in ("low_put_call_oi", "high_put_call_oi"):
        g = x[x["condition"] == condition]
        for horizon in ("next_session_return", "five_session_return"):
            vals = g[horizon].to_numpy(float)
            ordered = np.sort(vals)
            trim = int(len(vals) * 0.05)
            center = ordered[trim:len(vals) - trim] if trim else ordered
            out.append({"condition": condition, "horizon": horizon, "n": len(vals),
                        "mean": float(vals.mean()) if len(vals) else None,
                        "median": float(np.median(vals)) if len(vals) else None,
                        "trimmed_mean_5pct": float(center.mean()) if len(center) else None,
                        "unconditional_mean": float(x[horizon].mean()),
                        "difference_from_unconditional_bps":
                            float((vals.mean() - x[horizon].mean()) * 10000) if len(vals) else None,
                        "status": "TRAINING_ONLY_INDEX_PROXY_NOT_AN_EDGE"})
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="After-close OI positioning, sealed training data only")
    parser.add_argument("--index-database", type=Path, default=Path("data/financial_brain.duckdb"))
    parser.add_argument("--fo-database", type=Path, default=Path("data/fo_history.duckdb"))
    parser.add_argument("--start", type=date.fromisoformat, default=START)
    parser.add_argument("--end", type=date.fromisoformat, default=TRAIN_END)
    args = parser.parse_args(argv)
    research = duckdb.connect(str(args.index_database), read_only=True)
    try:
        ref = index_reference(research, start=args.start, end=args.end)
    finally:
        research.close()
    fo = duckdb.connect(str(args.fo_database), read_only=True)
    try:
        oi = option_oi(fo, ref, end=args.end)
    finally:
        fo.close()
    x = observations(ref, oi, start=args.start, end=args.end)
    print(json.dumps({"status": "TRAINING_ONLY_INDEX_PROXY_NOT_AN_EDGE",
                      "days_with_oi": len(oi), "eligible_days": len(x),
                      "holdout_queried": False, "results": diagnostic(x)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
