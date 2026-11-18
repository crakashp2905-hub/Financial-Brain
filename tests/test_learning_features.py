"""One definition per context dimension: the vector (labelling closed trades) and scalar (deciding live) versions agree."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from financial_brain.learning import features as F
from financial_brain.paper import loss_review as LR


def _random_trades(n: int = 600, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2026-10-05", periods=40).strftime("%Y-%m-%d")
    side = rng.choice(["long", "short"], n)
    px = rng.uniform(50, 3000, n)
    df = pd.DataFrame({
        "trade_id": [f"t{i}" for i in range(n)], "day": rng.choice(days, n), "side": side, "strategy": "ORB",
        "symbol": [f"S{i % 50}" for i in range(n)], "qty": rng.integers(1, 500, n), "entry_px": px,
        "entry_decision_px": px * (1 + rng.normal(0, 0.0003, n)), "entry_vwap": px * (1 + rng.normal(0, 0.004, n)),
        "net_pnl": rng.normal(0, 1000, n), "mkt_ret": rng.normal(0, 0.005, n),
        "daily_up": pd.array(rng.choice([True, False], n), dtype="boolean"),
        "gap": rng.normal(0, 0.01, n), "or_width": np.abs(rng.normal(0.012, 0.006, n)),
        "entry_minute": rng.integers(570, 660, n), "spread_bp": np.abs(rng.normal(4, 3, n)),
        "bucket": rng.choice(["mega", "large", "mid"], n)})
    # missing values in every nullable input
    for col in ("mkt_ret", "daily_up", "gap", "or_width", "spread_bp", "entry_vwap"):
        df.loc[rng.choice(n, 25, replace=False), col] = None
    return df


def _ctx(r, regime, vix):
    return {"side": r.side, "price": r.entry_decision_px, "vwap": r.entry_vwap, "mkt_ret": r.mkt_ret,
            "daily_up": None if pd.isna(r.daily_up) else bool(r.daily_up), "gap": r.gap, "or_width": r.or_width,
            "entry_minute": r.entry_minute, "spread_bp": r.spread_bp, "bucket": r.bucket, "day": r.day,
            "regime_prev": regime, "vix": vix}


def test_vector_and_scalar_agree_on_every_dimension_for_random_trades():
    df = _random_trades()
    days = sorted(df["day"].unique())
    rng = np.random.default_rng(1)
    ctx = {d: {"regime_prev": rng.choice(["RISK_ON", "NEUTRAL", "RISK_OFF", None]),
               "vix": float(rng.choice([12.0, 14.0, 17.5, 20.0, 26.0]))} for d in days}
    lab = F.label(df, ctx)
    bad = []
    for i, r in enumerate(df.itertuples()):
        c = _ctx(r, ctx[r.day]["regime_prev"], ctx[r.day]["vix"])
        for name in F.DIMENSIONS:
            v, s = lab.iloc[i][name], F.value_of(name, c)
            if not ((v is None or pd.isna(v)) and s is None) and v != s:
                bad.append((name, i, v, s))
    assert not bad, bad[:5]
    assert lab[list(F.DIMENSIONS)].notna().any().all(), "every dimension takes at least one value in this sample"


def test_bin_edges_are_right_closed_like_pandas_cut_and_match_the_loss_review_labels():
    df = _random_trades()
    lab, old = F.label(df), LR.label(df.assign(notional=df.entry_px * df.qty))
    for name in ("vs_market_today", "vs_daily_trend", "gap_size", "range_width", "entry_time", "direction"):
        a, b = lab[name].astype(object), old[name].astype(object)
        assert ((a == b) | (a.isna() & b.isna())).all(), name


@pytest.mark.parametrize("name,x,expected", [
    ("spread_bucket", 2.4, "spread < 2.5 bp"), ("spread_bucket", 2.5, "spread 2.5-5 bp"), ("spread_bucket", 5.0, "spread >= 5 bp"),
    ("vix_bucket", 13.9, "vix < 14"), ("vix_bucket", 14.0, "vix 14-20"), ("vix_bucket", 20.0, "vix >= 20")])
def test_the_edges_land_where_the_labels_say(name, x, expected):
    key = "spread_bp" if name == "spread_bucket" else "vix"
    assert F.value_of(name, {key: x}) == expected
    assert F.label(pd.DataFrame({"net_pnl": [1.0], "entry_px": [100.0], "qty": [1], "side": ["long"], "day": ["2026-11-02"],
                                 key: [x]}))[name].iloc[0] == expected


def test_a_missing_input_gives_no_value_not_a_guess():
    assert F.value_of("vs_market_today", {"side": "long"}) is None
    assert F.value_of("vs_vwap", {"side": "long", "price": 100.0}) is None
    assert F.value_of("regime_prev", {}) is None and F.value_of("weekday", {}) is None


def test_every_dimension_belongs_to_a_known_family_and_values_are_declared():
    assert {d.family for d in F.DIMENSIONS.values()} <= set(F.FAMILIES)
    lab = F.label(_random_trades(), {})
    for name, dim in F.DIMENSIONS.items():
        seen = set(lab[name].dropna().unique())
        assert seen <= set(dim.values), (name, seen - set(dim.values))


def test_cell_ids_round_trip():
    cid = F.cell_id("ORB", "vs_market_today", "against the market")
    assert F.parse_cell(cid) == ("ORB", "vs_market_today", "against the market")
