"""The signal screener: which stocks each algorithm and indicator fires on, sides, render safety, chart markers."""
import numpy as np
import pandas as pd

from financial_brain.paper import charts as CH
from financial_brain.paper import signals as SG


def bars(n=400, seed=0, tk="ABC"):
    rng = np.random.default_rng(seed)
    c = 100 * np.cumprod(1 + rng.normal(0.0005, 0.015, n))
    o = np.concatenate([[100.0], c[:-1]])
    return pd.DataFrame({"lineage": f"L{tk}", "ticker": tk, "d": pd.bdate_range("2024-01-01", periods=n).date, "o": o,
                         "h": np.maximum(o, c) * 1.006, "l": np.minimum(o, c) * 0.994, "c": c, "v": rng.uniform(1e5, 1e6, n)})


def test_signal_frame_sides_flips_and_signed_events():
    f = pd.DataFrame({"fvg_bull_new": [0, 0.4, 0, 0], "fvg_bear_retest": [0, 0, 1, 0], "supertrend_dir": [1, 1, -1, 1],
                      "fib_golden_pocket_60": [0, -1, -1, 0], "nr7": [0, 0, 0, 1]})
    sf = SG.signal_frame(f, {"macd_cross": pd.Series([0, 1, 1, 0.0])})
    assert list(sf["fvg_bull_new"]) == [0, 1, 0, 0] and list(sf["fvg_bear_retest"]) == [0, 0, -1, 0]
    assert list(sf["supertrend_dir"]) == [0, 0, -1, 1]                  # only the flips, first bar never fires
    assert list(sf["fib_golden_pocket_60"]) == [0, -1, 0, 0]            # on entry into the pocket only
    assert list(sf["algo:macd_cross"]) == [0, 1, 0, -1]
    assert SG.side_of("nr7", 1) == "watch" and SG.side_of("supertrend_dir", -1) == "sell"


def test_scan_lists_recent_firings_and_rule_holdings():
    b = pd.concat([bars(seed=1, tk="AAA"), bars(seed=2, tk="BBB")])
    hits, frames = SG.scan(b, window=5)
    assert set(frames) == {"AAA", "BBB"}
    fresh = hits[hits["side"] != "holding"]
    assert len(fresh) and fresh["ago"].between(0, 4).all()
    assert set(hits["side"]) <= {"buy", "sell", "watch", "holding"}
    last = {t: g["d"].iloc[-1] for t, g in b.groupby("ticker")}
    assert all(d <= last[t] for t, d in zip(hits["ticker"], hits["d"]))


def test_render_escapes_and_links_each_name_to_its_chart():
    hits = pd.DataFrame([{"signal": "fvg_bull_new", "ticker": "M&M", "lineage": "L1", "d": "2026-10-07", "ago": 0, "side": "buy", "close": 1.0},
                         {"signal": "algo:ema_ribbon", "ticker": "<x>", "lineage": "L2", "d": "2026-10-07", "ago": -1, "side": "holding", "close": 1.0}])
    page = SG.render(hits, "2026-10-07", strategies={"book: x": ["M&M"]}, n_names=2)
    assert "charts/M_M.html" in page and "<x>" not in page
    assert "Fair value gap, bullish (new)" in page and "EMA ribbon" in page


def test_chart_markers_include_every_indicator_that_fired():
    b = bars(seed=3)
    from financial_brain.features import library as LB
    from financial_brain.evaluation import rule_systems as RS
    f = LB.compute(b[["d", "o", "h", "l", "c", "v"]])
    sf = SG.signal_frame(f, RS.rules(b))
    m = CH.markers(b, sf=sf)
    assert any(k.startswith("indicator: ") for k in m) and "ma_cross_50_200" in m
    html = CH.page("ABC", b, sf=sf, fired=["Hammer (buy, 2026-10-07)"])
    assert "Firing on this stock now" in html and "Hammer (buy" in html
