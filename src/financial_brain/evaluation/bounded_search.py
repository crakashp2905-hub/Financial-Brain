"""Reproducible, training-only random search over comparable daily stock entry rules.

This is a *screen*, not an ADR-0004 trial. It samples rule parameters before outcomes
are loaded, uses 2016-2018 for selection and 2019-2021 for internal validation, and
never reads 2022+ prices. Options and intraday depth rules have incompatible data and
are reported as excluded rather than silently assigned a losing score.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import date
from itertools import product
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from . import rule_systems as RS
from . import family_validation as FV
from .script_mechanisms import TRAIN_END, daily_signals

SEED = 20261024
TRAIN_SPLIT = date(2018, 12, 31)
BASE_COST = 0.003  # 30 bp round trip, provisional; stressed at 60 bp below
STRESS_COST = 0.006
MIN_HALF_TRADES = 30
DEFAULT_SYMBOLS = 20


@dataclass(frozen=True)
class Variant:
    name: str
    family: str
    hold: int
    breakout: int = 55
    compression_window: int = 20
    ratio: float = 0.65
    volume_multiple: float = 1.0
    regime_ma: int = 200


def variants(*, seed: int = SEED, random_budget: int = 48) -> list[Variant]:
    """Freeze a bounded catalogue without looking at any returns."""
    grid = ([Variant(f"regime_b{w}_m{ma}_h{h}", "regime_breakout", h,
                     breakout=w, regime_ma=ma)
             for w, ma, h in product((20, 55, 100), (100, 200), (5, 10, 20))] +
            [Variant(f"compression_b{w}_r{r:.2f}_v{v:g}_h{h}", "compression_breakout", h,
                     compression_window=w, ratio=r, volume_multiple=v)
             for w, r, v, h in product((10, 20, 40), (0.55, 0.70, 0.85), (1.0, 1.5), (5, 10, 20))])
    if not 1 <= random_budget <= len(grid):
        raise ValueError("random budget must fit the declared parameter grid")
    rng = np.random.default_rng(seed)
    chosen = sorted(int(i) for i in rng.choice(len(grid), size=random_budget, replace=False))
    fixed = [Variant(f"fixed_{name}_h{h}", "fixed_rule", h)
             for name, h in product(RS.rules(_dummy_bars()).keys(), (5, 10, 20))]
    return [grid[i] for i in chosen] + fixed


def _dummy_bars() -> pd.DataFrame:
    """Enough bars to obtain the fixed registry without consulting market outcomes."""
    n = 210
    return pd.DataFrame({"o": np.ones(n), "h": np.ones(n), "l": np.ones(n),
                         "c": np.ones(n), "v": np.ones(n)})


def choose_symbols(basis: pd.DataFrame, *, n: int = DEFAULT_SYMBOLS, seed: int = SEED) -> list[str]:
    """Sample from stocks present in the F&O roster at training start."""
    historic = basis[pd.to_datetime(basis["start"]) <= pd.Timestamp("2016-01-01")]
    names = sorted(set(historic["symbol"]) - {"NIFTY"})
    if n < 1 or n > len(names):
        raise ValueError("requested sample exceeds the historical universe")
    return sorted(np.random.default_rng(seed).choice(names, size=n, replace=False).tolist())


def load_data(symbols: list[str], *, db: Path) -> tuple[dict[str, pd.DataFrame], pd.Series]:
    """Read adjusted NSE EQ OHLCV and Nifty closes through 2021 only."""
    if not db.exists():
        raise FileNotFoundError(db)
    con = duckdb.connect(str(db), read_only=True)
    try:
        raw = con.execute("""SELECT e.ticker AS symbol, e.trade_date AS session,
                   e.open_price*a.factor AS open, e.high_price*a.factor AS high,
                   e.low_price*a.factor AS low, e.close_price*a.factor AS close,
                   e.traded_volume AS volume
            FROM eod_prices e JOIN adjusted_prices a
              ON a.business_date=e.trade_date AND a.isin=e.isin AND a.ticker=e.ticker
            WHERE e.exchange='NSE' AND e.series='EQ' AND a.factor>0
              AND e.ticker IN (SELECT UNNEST(?))
              AND e.trade_date >= '2016-01-01' AND e.trade_date < '2022-01-01'
            QUALIFY ROW_NUMBER() OVER (PARTITION BY e.ticker,e.trade_date
                                      ORDER BY e.turnover DESC NULLS LAST)=1
            ORDER BY symbol,session""", [symbols]).df()
        index = con.execute("""SELECT business_date AS session, close_level FROM index_levels_canonical
            WHERE index_name='Nifty 50' AND variant='PRICE'
              AND business_date >= '2015-01-01' AND business_date < '2022-01-01'
            ORDER BY business_date""").df()
    finally:
        con.close()
    if index.empty:
        raise ValueError("Nifty training series is missing")
    index["session"] = pd.to_datetime(index["session"])
    market = index.set_index("session")["close_level"]
    panel = {}
    for symbol, group in raw.groupby("symbol"):
        bars = group.drop(columns="symbol").reset_index(drop=True)
        bars["session"] = pd.to_datetime(bars["session"])
        if len(bars) >= 1400 and bars["session"].dt.date.max() <= TRAIN_END:
            panel[symbol] = bars
    return panel, market


def events(bars: pd.DataFrame, market: pd.Series, variant: Variant,
           fixed: dict[str, pd.Series] | None = None) -> pd.Series:
    """Return one close-confirmed entry event per signal, never a fitted outcome."""
    if variant.family == "fixed_rule":
        name = variant.name.removeprefix("fixed_").rsplit("_h", 1)[0]
        positions = fixed if fixed is not None else RS.rules(bars.rename(columns={
            "open": "o", "high": "h", "low": "l", "close": "c", "volume": "v"}))
        p = positions[name].reset_index(drop=True).fillna(0)
        return ((p > 0) & (p.shift(fill_value=0) <= 0)).astype(bool)
    ma = market.rolling(variant.regime_ma, min_periods=variant.regime_ma).mean()
    state = (market > ma).map({True: "BULL", False: "SIDEWAYS"})
    dates = pd.to_datetime(bars["session"])
    regime = state.reindex(dates, method="ffill")
    if regime.isna().any():
        raise ValueError("market regime missing for a stock session")
    signals = daily_signals(bars, regime, breakout_window=variant.breakout,
                            compression_window=variant.compression_window,
                            compression_ratio=variant.ratio,
                            volume_multiple=variant.volume_multiple)
    return signals[variant.family].reset_index(drop=True)


def evaluate(panel: dict[str, pd.DataFrame], market: pd.Series,
             catalogue: list[Variant], *, cost: float = BASE_COST) -> pd.DataFrame:
    """Next-open event outcomes vs equal-weight cohort, no overlapping capital model."""
    if not 0 <= cost < 1:
        raise ValueError("invalid cost")
    # On each event date, buy the same historical sample for the same horizon.
    # Both sides pay the same provisional round trip, so the comparison is
    # schedule-matched and does not reward a strategy merely for a bull market.
    benchmark = {}
    for hold in {v.hold for v in catalogue}:
        cohort = []
        for bars in panel.values():
            ret = bars["close"].shift(-(1 + hold)) / bars["open"].shift(-1) - 1 - cost
            cohort.append(pd.Series(ret.to_numpy(), index=bars["session"]))
        benchmark[hold] = pd.concat(cohort, axis=1).mean(axis=1)
    rows = []
    for symbol, bars in panel.items():
        if bars["session"].dt.date.max() > TRAIN_END:
            raise ValueError("evaluation cannot inspect the sealed holdout")
        fixed = RS.rules(bars.rename(columns={"open": "o", "high": "h", "low": "l",
                                               "close": "c", "volume": "v"}))
        for variant in catalogue:
            signal = events(bars, market, variant, fixed)
            entry = bars["open"].shift(-1)
            exit_ = bars["close"].shift(-(1 + variant.hold))
            selected = signal & entry.notna() & exit_.notna() & (entry > 0) & (exit_ > 0)
            for i in np.flatnonzero(selected.to_numpy()):
                day = bars["session"].iloc[i]
                base = benchmark[variant.hold].get(day)
                if base is None or not np.isfinite(base):
                    raise ValueError("schedule-matched benchmark is missing")
                net = float(exit_.iloc[i] / entry.iloc[i] - 1 - cost)
                rows.append((variant.name, variant.family, symbol, day, net, float(base), net - float(base)))
    return pd.DataFrame(rows, columns=["variant", "family", "symbol", "signal_day",
                                       "net", "benchmark_net", "excess_net"])


def _period(events_: pd.DataFrame, *, start: str, end: str, variants_: list[Variant]) -> list[dict]:
    sub = events_[(events_["signal_day"] >= start) & (events_["signal_day"] <= end)].copy()
    out = []
    for v in variants_:
        x = sub[sub["variant"] == v.name]
        monthly = x.groupby(x["signal_day"].dt.to_period("M"))["excess_net"].mean() if len(x) else pd.Series(dtype=float)
        t = float(monthly.mean() / (monthly.std(ddof=1) / np.sqrt(len(monthly)))) if len(monthly) >= 6 and monthly.std(ddof=1) > 0 else None
        out.append({"variant": v.name, "family": v.family, "trades": len(x),
                    "months": len(monthly), "mean_net_per_trade": float(x["net"].mean()) if len(x) else None,
                    "mean_excess_per_trade": float(x["excess_net"].mean()) if len(x) else None,
                    "mean_monthly_excess_trade_average": float(monthly.mean()) if len(monthly) else None,
                    "monthly_t_descriptive": t})
    return out


def summarize(results: pd.DataFrame, catalogue: list[Variant]) -> dict:
    """Select on 2016-2018 only; report 2019-2021 without reselecting."""
    training = _period(results, start="2016-01-01", end="2018-12-31", variants_=catalogue)
    validation = {r["variant"]: r for r in _period(results, start="2019-01-01", end="2021-12-31", variants_=catalogue)}
    eligible = [r for r in training if r["trades"] >= MIN_HALF_TRADES
                and r["monthly_t_descriptive"] is not None
                and r["mean_net_per_trade"] > 0 and r["mean_excess_per_trade"] > 0
                and r["mean_monthly_excess_trade_average"] > 0]
    winner = max(eligible, key=lambda r: r["monthly_t_descriptive"]) if eligible else None
    return {"status": "TRAINING_SEARCH_ONLY_NOT_A_TRIAL", "searched_variants": len(catalogue),
            "eligible_training_variants": len(eligible), "train_period": "2016-2018",
            "internal_validation_period": "2019-2021", "selected_on_training": winner,
            "same_variant_internal_validation": validation[winner["variant"]] if winner else None,
            "family_counts": {f: sum(v.family == f for v in catalogue)
                              for f in sorted({v.family for v in catalogue})},
            "all_variants": [{**asdict(v), "training": t, "internal_validation": validation[v.name]}
                             for v, t in zip(catalogue, training)]}


def selected_diagnostics(results: pd.DataFrame, name: str) -> dict:
    """Expose concentration and tails for the training-selected variant only."""
    x = results[(results["variant"] == name) & (results["signal_day"] >= "2019-01-01") &
                (results["signal_day"] <= "2021-12-31")].copy()
    if x.empty:
        return {"trades": 0, "status": "NO_INTERNAL_VALIDATION_TRADES"}
    ordered = x["net"].sort_values().reset_index(drop=True)
    trim = int(len(ordered) * 0.05)
    trimmed = ordered.iloc[trim:len(ordered) - trim] if trim else ordered
    years = x.groupby(x["signal_day"].dt.year)["net"].agg(["count", "mean", "median"])
    symbols = x.groupby("symbol")["net"].agg(["count", "mean"]).sort_values("count", ascending=False)
    positive = x.loc[x["net"] > 0, "net"].sum()
    worst, best = x.loc[x["net"].idxmin()], x.loc[x["net"].idxmax()]
    return {"trades": len(x), "median_net_per_trade": float(ordered.median()),
            "median_excess_per_trade": float(x["excess_net"].median()),
            "mean_excess_per_trade": float(x["excess_net"].mean()),
            "trimmed_5pct_mean": float(trimmed.mean()), "win_fraction": float((x["net"] > 0).mean()),
            "worst_trade": float(ordered.iloc[0]), "best_trade": float(ordered.iloc[-1]),
            "worst_trade_context": {"symbol": str(worst["symbol"]), "signal_day": str(worst["signal_day"].date())},
            "best_trade_context": {"symbol": str(best["symbol"]), "signal_day": str(best["signal_day"].date())},
            "top_5_share_of_positive_pnl": float(x["net"].nlargest(5).sum() / positive) if positive > 0 else None,
            "by_year": {str(int(y)): {"trades": int(r["count"]), "mean": float(r["mean"]),
                                     "median": float(r["median"])} for y, r in years.iterrows()},
            "top_symbols_by_count": {s: {"trades": int(r["count"]), "mean": float(r["mean"])}
                                     for s, r in symbols.head(5).iterrows()}}


def multiplicity_diagnostic(results: pd.DataFrame, catalogue: list[Variant]) -> dict:
    """Exploratory RC/PBO on monthly *mean trade returns*, not portfolio P&L."""
    if results.empty:
        return {"status": "NO_EVENTS"}
    x = results.copy()
    x["month"] = x["signal_day"].dt.to_period("M")
    matrix = x.pivot_table(index="month", columns="variant", values="excess_net", aggfunc="mean")
    months = pd.period_range("2016-01", "2021-12", freq="M")
    matrix = matrix.reindex(index=months, columns=[v.name for v in catalogue]).fillna(0.0)
    # Zero denotes no entry in that month, not an imputed fill.
    val = matrix.loc["2019-01":"2021-12"]
    try:
        rc = FV.reality_check(val, reps=500, seed=SEED)
        pbo = FV.pbo(matrix, blocks=8)
    except (ValueError, FloatingPointError):
        return {"status": "INSUFFICIENT_FOR_MULTIPLICITY_DIAGNOSTIC"}
    return {"status": "EXPLORATORY_NOT_PORTFOLIO_SIGNIFICANCE", "validation_reality_check": rc,
            "training_period_pbo": pbo, "monthly_definition": "mean net excess event return; no-event months zero"}


def run(*, basis_path: Path = Path("data/basis_contracts.parquet"),
        research_db: Path = Path("data/financial_brain.duckdb"),
        sample_size: int = DEFAULT_SYMBOLS, random_budget: int = 48,
        seed: int = SEED) -> dict:
    """Read-only search; no trial, alerts, orders or persistent output."""
    symbols = choose_symbols(pd.read_parquet(basis_path), n=sample_size, seed=seed)
    catalogue = variants(seed=seed, random_budget=random_budget)
    panel, market = load_data(symbols, db=research_db)
    if not panel:
        raise ValueError("no sampled symbols have sufficient training data")
    events_ = evaluate(panel, market, catalogue)
    result = summarize(events_, catalogue)
    winner = result["selected_on_training"]
    if winner:
        result["selected_variant_diagnostics"] = selected_diagnostics(events_, winner["variant"])
        selected = [v for v in catalogue if v.name == winner["variant"]]
        stress = summarize(evaluate(panel, market, selected, cost=STRESS_COST), selected)
        result["selected_variant_stress_cost"] = stress["all_variants"][0]["internal_validation"]
    else:
        result["selected_variant_stress_cost"] = None
        result["selected_variant_diagnostics"] = None
    result["multiplicity_diagnostic"] = multiplicity_diagnostic(events_, catalogue)
    result.update({"seed": seed, "sampled_symbols": symbols, "usable_symbols": sorted(panel),
                   "round_trip_cost": BASE_COST, "stress_round_trip_cost": STRESS_COST,
                   "benchmark": "same-date, same-horizon equal-weight 2016-start F&O stock cohort net of same cost",
                   "excluded": [
                       "options: no sealed historical executable two-sided book",
                       "intraday liquidity filter: no sealed decision-time quote/depth and frozen baseline P&L",
                       "pairs, carry and portfolio overlays: not comparable under single-stock entry/exit convention"],
                   "limitations": "2016-start F&O convenience sample; adjusted factors are not audited PIT; "
                                  "entry events reinterpret 11 fixed position rules; overlapping trades and "
                                  "capital constraints are not modelled. Not a deployable strategy."})
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded 2016-2021 training-only random search")
    parser.add_argument("--symbols", type=int, default=DEFAULT_SYMBOLS)
    parser.add_argument("--random-budget", type=int, default=48)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--output", type=Path, help="optional complete audit JSON; default prints summary only")
    args = parser.parse_args(argv)
    result = run(sample_size=args.symbols, random_budget=args.random_budget, seed=args.seed)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "all_variants"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
