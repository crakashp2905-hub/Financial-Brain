"""Chart pages: every overlay drawn, every rule's buys and sells marked, levels as of the last bar, safe HTML."""
import json
import re

import numpy as np
import pandas as pd

from financial_brain.paper import charts as CH


def bars(n=400, seed=0):
    rng = np.random.default_rng(seed)
    c = 100 * np.cumprod(1 + rng.normal(0.0005, 0.015, n))
    o = np.concatenate([[100.0], c[:-1]])
    return pd.DataFrame({"d": pd.bdate_range("2024-01-01", periods=n).date, "o": o, "h": np.maximum(o, c) * 1.006,
                         "l": np.minimum(o, c) * 0.994, "c": c, "v": rng.uniform(1e5, 1e6, n)})


def test_the_page_carries_every_overlay_pane_and_rule_marker():
    html = CH.page("ABC", bars(), why="+ 12-month momentum high", table=[{"feature": "rsi_14", "value": 55.0,
                   "percentile": 0.6, "bullish_when": "low", "evidence": "not significant"}])
    data = json.loads(re.search(r"const D = (\{.*?\});\n", html, re.S).group(1))
    for k in ("sma20", "sma50", "sma200", "bb_up", "tenkan", "span_a", "supertrend", "psar", "vwap20", "rsi", "macd", "macd_hist"):
        assert data["s"][k], k
    assert len(data["markers"]) == 11 and any(data["markers"].values())
    assert any(x["title"].startswith("Fib") for x in data["levels"]) and any(x["title"] == "Pivot" for x in data["levels"])
    assert "12-month momentum" in html and "rsi_14" in html


def test_markers_alternate_buy_then_sell():
    m = CH.markers(bars())["ma_cross_50_200"]
    texts = [x["text"] for x in sorted(m, key=lambda x: x["time"])]
    assert all(a != b for a, b in zip(texts, texts[1:]))


def test_untrusted_text_cannot_break_the_page():
    html = CH.page("ABC</script><script>alert(1)</script>", bars(), why="<img src=x onerror=alert(1)>")
    assert "<img src=x" not in html and "</script><script>alert" not in html
