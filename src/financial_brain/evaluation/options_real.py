"""Exploratory NIFTY call/put research using observed NSE F&O daily closes.

This is a *training-only* diagnostic, not a registered trial or executable quote.
Contract selection uses information available at the signal close; entry is the next
session's close and exit is five sessions later. EOD closes are not bid/ask fills.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

TRAIN_END = "2021-12-31"
DEFAULT_START = "2016-01-01"
DEFAULT_HOLD = 5
DEFAULT_MIN_CONTRACTS = 100


def load_observed(con: duckdb.DuckDBPyConnection, *, reference: pd.DataFrame | None = None,
                  start: str = DEFAULT_START,
                  end: str = TRAIN_END, hold: int = DEFAULT_HOLD,
                  min_contracts: int = DEFAULT_MIN_CONTRACTS) -> pd.DataFrame:
    """One near-expiry ATM CE/PE per signal date with observed entry and exit closes.

    The training boundary applies to *all* observations, including exit prices.
    Missing or untraded entry/exit contracts are excluded, never forward-filled.
    """
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if last > date.fromisoformat(TRAIN_END) or first > last:
        raise ValueError("only the sealed 2016-2021 training range may be explored")
    if not 1 <= hold <= 20 or min_contracts < 1:
        raise ValueError("invalid holding period or contract liquidity threshold")
    if reference is not None:
        if not {"trade_date", "spot"}.issubset(reference):
            raise ValueError("reference requires trade_date and spot")
        ref_days = pd.to_datetime(reference["trade_date"]).dt.date
        if ref_days.duplicated().any() or (not reference.empty and ref_days.max() > last):
            raise ValueError("reference dates must be unique and remain inside training")
        con.register("_index_reference", reference)
        spot_sql = "SELECT trade_date, spot FROM _index_reference WHERE spot > 0"
        params = []
    else:
        # Fixture fallback only; front-future rolls make this unsuitable as an index signal.
        spot_sql = """SELECT trade_date, ARG_MIN(close, expiry) AS spot FROM fo_bhav
                      WHERE instrument = 'FUTIDX' AND symbol = 'NIFTY'
                        AND trade_date BETWEEN ?::DATE - INTERVAL 20 DAY AND ?::DATE
                        AND close > 0 AND contracts > 0 AND expiry > trade_date
                      GROUP BY trade_date"""
        params = [start, end]
    query = f"""
    WITH spot AS ({spot_sql}), timeline AS (
        SELECT trade_date AS signal_date, spot,
               LAG(spot, 5) OVER (ORDER BY trade_date) AS spot_5_ago,
               LEAD(trade_date, 1) OVER (ORDER BY trade_date) AS entry_date,
               LEAD(trade_date, ?) OVER (ORDER BY trade_date) AS exit_date
        FROM spot
    ), ranked AS (
        SELECT t.*, o.expiry, o.strike, o.option_type,
               ROW_NUMBER() OVER (
                   PARTITION BY t.signal_date, o.option_type
                   ORDER BY o.expiry, ABS(o.strike - t.spot), o.strike
               ) AS rank
        FROM timeline t JOIN fo_bhav o ON o.trade_date = t.signal_date
        WHERE o.instrument = 'OPTIDX' AND o.symbol = 'NIFTY'
          AND o.option_type IN ('CE', 'PE') AND o.contracts >= ?
          AND o.close > 0 AND o.expiry >= t.signal_date + (? * INTERVAL 2 DAY)
          AND t.signal_date >= ?::DATE AND t.spot_5_ago > 0
          AND t.exit_date <= ?::DATE
    )
    SELECT r.signal_date, r.entry_date, r.exit_date, r.spot, r.spot_5_ago,
           r.expiry, r.strike, r.option_type,
           e.close AS entry_close, x.close AS exit_close,
           e.contracts AS entry_contracts, x.contracts AS exit_contracts
    FROM ranked r
    LEFT JOIN fo_bhav e ON e.trade_date = r.entry_date AND e.instrument = 'OPTIDX'
       AND e.symbol = 'NIFTY' AND e.expiry = r.expiry AND e.strike = r.strike
       AND e.option_type = r.option_type
    LEFT JOIN fo_bhav x ON x.trade_date = r.exit_date AND x.instrument = 'OPTIDX'
       AND x.symbol = 'NIFTY' AND x.expiry = r.expiry AND x.strike = r.strike
       AND x.option_type = r.option_type
    WHERE r.rank = 1 AND r.expiry > r.exit_date
    ORDER BY r.signal_date, r.option_type
    """
    try:
        out = con.execute(query, [*params, hold, min_contracts, hold, start, end]).df()
    finally:
        if reference is not None:
            con.unregister("_index_reference")
    out["executable_proxy"] = (out["entry_close"].gt(0) & out["exit_close"].gt(0)
                               & out["entry_contracts"].ge(min_contracts)
                               & out["exit_contracts"].ge(min_contracts))
    return out


def candidates(observed: pd.DataFrame, *, haircut_per_side: float = 0.02) -> pd.DataFrame:
    """Four *long-option* hypotheses: trend/reversal x calls/puts.

    A 2% premium haircut on both entry and exit is a stress assumption, not measured
    Indian spread or the complete brokerage/STT/GST/stamp/settlement bill. Selling here
    means closing the purchased option; naked option writing is deliberately absent.
    """
    if not 0 <= haircut_per_side < 0.5:
        raise ValueError("haircut must be a fraction between zero and one half")
    if observed.empty:
        return pd.DataFrame(columns=["signal_date", "family", "option_type", "net_proxy"])
    required = {"signal_date", "spot", "spot_5_ago", "option_type", "entry_close",
                "exit_close", "executable_proxy"}
    if not required.issubset(observed):
        raise ValueError(f"missing option fields: {sorted(required - set(observed))}")
    o = observed.copy()
    o = o[(o["spot"] > 0) & (o["spot_5_ago"] > 0) & o["option_type"].isin(["CE", "PE"])]
    o["trend"] = np.where(o["spot"] > o["spot_5_ago"], "up",
                          np.where(o["spot"] < o["spot_5_ago"], "down", "flat"))
    o = o[o["trend"] != "flat"].copy()
    o["net_proxy"] = np.where(o["executable_proxy"],
                              (o["exit_close"] * (1 - haircut_per_side)
                               - o["entry_close"] * (1 + haircut_per_side)) / (
                                   o["entry_close"] * (1 + haircut_per_side)), np.nan)
    direction = np.where(o["option_type"] == "CE", "up", "down")
    o["family"] = np.where(o["trend"] == direction, "five_day_trend", "five_day_reversal")
    return o.drop(columns="trend")


def diagnostic(rows: pd.DataFrame) -> list[dict]:
    """Training-only descriptive results; no edge verdict or holdout access."""
    result = []
    groups = ["family", "option_type"] + (["structure"] if "structure" in rows else [])
    for labels, g in rows.groupby(groups):
        family, kind, *other = labels
        r = g["net_proxy"].dropna().to_numpy(dtype=float)
        early = g[pd.to_datetime(g["signal_date"]) < pd.Timestamp("2019-01-01")]["net_proxy"].dropna()
        late = g[pd.to_datetime(g["signal_date"]) >= pd.Timestamp("2019-01-01")]["net_proxy"].dropna()
        ordered = np.sort(r)
        trim = int(len(ordered) * 0.05)
        center = ordered[trim:len(ordered) - trim] if trim else ordered
        result.append({"family": family, "option_type": kind,
                       **({"structure": other[0]} if other else {}), "signals": len(g),
                       "priced_exits": len(r), "coverage": len(r) / len(g),
                       "mean_proxy": float(r.mean()) if len(r) else None,
                       "early_mean": float(early.mean()) if len(early) else None,
                       "late_mean": float(late.mean()) if len(late) else None,
                       "trimmed_mean_5pct": float(center.mean()) if len(center) else None,
                       "missing_exit_loss_mean": float((r.sum() - (len(g) - len(r))) / len(g)),
                       "median_proxy": float(np.median(r)) if len(r) else None,
                       "positive_fraction": float((r > 0).mean()) if len(r) else None,
                       "worst_proxy": float(r.min()) if len(r) else None,
                       "status": "TRAINING_ONLY_NOT_AN_EDGE"})
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Observed NIFTY option-close exploration, training dates only")
    parser.add_argument("--database", type=Path, default=Path("data/fo_history.duckdb"))
    parser.add_argument("--index-database", type=Path, default=Path("data/financial_brain.duckdb"))
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=TRAIN_END)
    parser.add_argument("--hold", type=int, default=DEFAULT_HOLD)
    parser.add_argument("--min-contracts", type=int, default=DEFAULT_MIN_CONTRACTS)
    parser.add_argument("--haircut", type=float, default=0.02)
    parser.add_argument("--verticals", action="store_true", help="include same-expiry defined-risk spreads")
    args = parser.parse_args(argv)
    index_con = duckdb.connect(str(args.index_database), read_only=True)
    try:
        reference = index_con.execute("""SELECT business_date AS trade_date, close_level AS spot
            FROM index_levels_canonical WHERE index_name = 'Nifty 50' AND variant = 'PRICE'
              AND business_date BETWEEN ?::DATE - INTERVAL 20 DAY AND ?::DATE
              AND close_level > 0 ORDER BY business_date""", [args.start, args.end]).df()
    finally:
        index_con.close()
    if reference.empty:
        raise ValueError("no Nifty 50 PRICE index reference; refusing futures-roll fallback")
    con = duckdb.connect(str(args.database), read_only=True)
    try:
        observed = load_observed(con, reference=reference, start=args.start, end=args.end, hold=args.hold,
                                 min_contracts=args.min_contracts)
        if args.verticals:
            from .options_vertical import load_wings, vertical_candidates
            wings = load_wings(con, observed, min_contracts=args.min_contracts)
            spreads = vertical_candidates(wings, haircut_per_side=args.haircut,
                                          min_contracts=args.min_contracts)
            if not spreads.empty:
                spreads["family"] = np.where(spreads["spot"] > spreads["spot_5_ago"],
                                             "five_day_up", "five_day_down")
    finally:
        con.close()
    rows = candidates(observed, haircut_per_side=args.haircut)
    print(json.dumps({"status": "TRAINING_ONLY_NOT_AN_EDGE", "rows": len(rows),
                      "holdout_queried": False, "haircut_per_side": args.haircut,
                      "results": diagnostic(rows),
                      **({"vertical_results": diagnostic(spreads) if not spreads.empty else []}
                         if args.verticals else {})}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
