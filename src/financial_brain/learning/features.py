"""The context a trade is taken in: one definition of every dimension, used both to label past trades and to decide live.

A lesson such as "ORB trades taken against the market lose" is only usable if the thing it names can be computed at the moment
of the decision, with the same meaning as when the trades were labelled. That is the failure this module exists to prevent: a
rule found on a pandas column and applied by different code at run time drifts, silently. Each :class:`Dimension` therefore has
two implementations, ``vector`` (a DataFrame of closed trades) and ``scalar`` (one decision as it happens), and a test feeds both
the same random inputs and requires identical answers.

Every dimension is known at the decision time. Anything that is only known after the fill (the slippage a trade suffered, the
cost of walking the book for the quantity actually sized) is an *outcome*, not context, and is not here.

Bins are fixed constants, chosen from the shape of the feature (for spread, its quartiles in the first month of live trades), never
from how trades in a bin performed.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

FAMILIES = ("execution", "market", "setup", "calendar")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _missing(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x)) or x is pd.NA


@dataclass(frozen=True)
class Dimension:
    name: str
    family: str
    doc: str
    values: tuple[str, ...]
    vector: Callable[[pd.DataFrame], pd.Series]
    scalar: Callable[[Mapping], str | None]
    #: "trade": the value varies between trades on one day, so a cell can be compared with the other trades that day.
    #: "day": one value for the whole session (weekday, regime, VIX): a cell can only be compared with *other days*.
    level: str = "trade"


def _where(mask_none: pd.Series, out: np.ndarray) -> pd.Series:
    s = pd.Series(out, index=mask_none.index, dtype=object)
    s[mask_none.to_numpy()] = None
    return s


def _bin(x: pd.Series, edges: tuple[float, float], labels: tuple[str, str, str]) -> pd.Series:
    """Right-closed bins (-inf, e0], (e0, e1], (e1, inf), as ``pandas.cut`` does."""
    v = x.to_numpy(dtype=float)
    out = np.where(v <= edges[0], labels[0], np.where(v <= edges[1], labels[1], labels[2])).astype(object)
    return _where(x.isna(), out)


def _bin_scalar(x, edges: tuple[float, float], labels: tuple[str, str, str]) -> str | None:
    if _missing(x):
        return None
    x = float(x)
    return labels[0] if x <= edges[0] else labels[1] if x <= edges[1] else labels[2]


# ------------------------------------------------------------------------------------------------ the dimensions

GAP_LABELS = ("gap < 0.5%", "gap 0.5-1.5%", "gap > 1.5%")
RANGE_LABELS = ("range < 0.8%", "range 0.8-1.5%", "range > 1.5%")
TIME_LABELS = ("09:30-09:44", "09:45-10:29", "10:30-11:00")
SPREAD_LABELS = ("spread < 2.5 bp", "spread 2.5-5 bp", "spread >= 5 bp")
VIX_LABELS = ("vix < 14", "vix 14-20", "vix >= 20")


def _is_long(df: pd.DataFrame) -> pd.Series:
    return df["side"] == "long"


def _v_market(df):
    up = _is_long(df)
    out = np.where((df["mkt_ret"].to_numpy(dtype=float) > 0) == up.to_numpy(), "with the market", "against the market")
    return _where(df["mkt_ret"].isna(), out.astype(object))


def _s_market(c):
    if _missing(c.get("mkt_ret")):
        return None
    return "with the market" if (float(c["mkt_ret"]) > 0) == (c["side"] == "long") else "against the market"


def _v_trend(df):
    up = _is_long(df).to_numpy()
    d = df["daily_up"].astype("boolean")
    out = np.where(d.fillna(False).to_numpy() == up, "with the trend", "against the trend")
    return _where(d.isna(), out.astype(object))


def _s_trend(c):
    if _missing(c.get("daily_up")):
        return None
    return "with the trend" if bool(c["daily_up"]) == (c["side"] == "long") else "against the trend"


def _v_regime(df):
    s = df["regime_prev"] if "regime_prev" in df else pd.Series(None, index=df.index, dtype=object)
    return s.map(lambda x: None if _missing(x) else f"regime {x}").astype(object)


def _s_regime(c):
    return None if _missing(c.get("regime_prev")) else f"regime {c['regime_prev']}"


def _v_vix(df):
    x = pd.to_numeric(df["vix"], errors="coerce") if "vix" in df else pd.Series(np.nan, index=df.index)
    return _bin(x, (13.999999, 19.999999), VIX_LABELS)


def _s_vix(c):
    return _bin_scalar(c.get("vix"), (13.999999, 19.999999), VIX_LABELS)


def _v_vwap(df):
    px = df["entry_decision_px"].where(df["entry_decision_px"].notna(), df["entry_px"]) \
        if "entry_decision_px" in df else df["entry_px"]
    vw = pd.to_numeric(df["entry_vwap"], errors="coerce") if "entry_vwap" in df else pd.Series(np.nan, index=df.index)
    long = _is_long(df).to_numpy()
    with_vwap = np.where(long, px.to_numpy(dtype=float) >= vw.to_numpy(dtype=float),
                         px.to_numpy(dtype=float) <= vw.to_numpy(dtype=float))
    return _where(vw.isna() | px.isna(), np.where(with_vwap, "with VWAP", "against VWAP").astype(object))


def _s_vwap(c):
    if _missing(c.get("vwap")) or _missing(c.get("price")):
        return None
    ok = float(c["price"]) >= float(c["vwap"]) if c["side"] == "long" else float(c["price"]) <= float(c["vwap"])
    return "with VWAP" if ok else "against VWAP"


def _v_weekday(df):
    return pd.to_datetime(df["day"]).dt.day_of_week.map(lambda i: WEEKDAYS[i]).astype(object)


def _s_weekday(c):
    return None if _missing(c.get("day")) else WEEKDAYS[date.fromisoformat(str(c["day"])[:10]).weekday()]


def _make() -> dict[str, Dimension]:
    d: dict[str, Dimension] = {}

    def add(name, family, doc, values, vector, scalar, level="trade"):
        d[name] = Dimension(name, family, doc, tuple(values), vector, scalar, level)

    add("spread_bucket", "execution", "quoted bid-ask spread at the decision, bp of the mid", SPREAD_LABELS,
        lambda df: _bin(pd.to_numeric(df["spread_bp"], errors="coerce"), (2.4999999, 4.9999999), SPREAD_LABELS),
        lambda c: _bin_scalar(c.get("spread_bp"), (2.4999999, 4.9999999), SPREAD_LABELS))
    add("liquidity", "execution", "the name's liquidity bucket", ("liquidity mega", "liquidity large", "liquidity mid",
                                                                  "liquidity small"),
        lambda df: df["bucket"].map(lambda x: None if _missing(x) else f"liquidity {x}").astype(object),
        lambda c: None if _missing(c.get("bucket")) else f"liquidity {c['bucket']}")
    add("vs_vwap", "execution", "entry price on the trade's side of the day's VWAP", ("with VWAP", "against VWAP"),
        _v_vwap, _s_vwap)
    add("vs_market_today", "market", "the trade's side against the equal-weighted market's move since the open",
        ("with the market", "against the market"), _v_market, _s_market)
    add("vs_daily_trend", "market", "the trade's side against the market's position versus its 20-session average",
        ("with the trend", "against the trend"), _v_trend, _s_trend)
    add("regime_prev", "market", "the previous session's market regime label",
        tuple(f"regime {r}" for r in ("RISK_ON", "NEUTRAL", "NARROW", "RISK_OFF", "CRISIS")), _v_regime, _s_regime, "day")
    add("vix_bucket", "market", "India VIX at the previous close", VIX_LABELS, _v_vix, _s_vix, "day")
    add("gap_size", "setup", "the name's absolute gap at the open", GAP_LABELS,
        lambda df: _bin(df["gap"].abs(), (0.005, 0.015), GAP_LABELS),
        lambda c: _bin_scalar(None if _missing(c.get("gap")) else abs(float(c["gap"])), (0.005, 0.015), GAP_LABELS))
    add("range_width", "setup", "the opening range as a share of the open", RANGE_LABELS,
        lambda df: _bin(df["or_width"], (0.008, 0.015), RANGE_LABELS),
        lambda c: _bin_scalar(c.get("or_width"), (0.008, 0.015), RANGE_LABELS))
    add("entry_time", "setup", "the minute of entry", TIME_LABELS,
        lambda df: _bin(df["entry_minute"], (584, 629), TIME_LABELS),
        lambda c: _bin_scalar(c.get("entry_minute"), (584, 629), TIME_LABELS))
    add("direction", "setup", "long or short", ("long", "short"), lambda df: df["side"].astype(object),
        lambda c: c.get("side"))
    add("weekday", "calendar", "day of the week", WEEKDAYS[:5], _v_weekday, _s_weekday, "day")
    return d


DIMENSIONS: dict[str, Dimension] = _make()


def value_of(name: str, ctx: Mapping) -> str | None:
    """One decision's value on one dimension. ``ctx``: side, price, vwap, mkt_ret, daily_up, gap, or_width, entry_minute,
    spread_bp, bucket, day, regime_prev, vix (any may be missing; the answer is then None)."""
    return DIMENSIONS[name].scalar(ctx)


INPUT_COLUMNS = ("side", "mkt_ret", "daily_up", "gap", "or_width", "entry_minute", "spread_bp", "bucket", "entry_vwap",
                 "entry_decision_px", "entry_px", "day", "regime_prev", "vix")


def label(df: pd.DataFrame, day_context: Mapping[str, Mapping] | None = None) -> pd.DataFrame:
    """Closed trades with one column per dimension and ``net_bp``. ``day_context``: day -> {regime_prev, vix}."""
    d = df.copy()
    for col in INPUT_COLUMNS:                           # a frame without one of the inputs gets "unknown", not an error
        if col not in d:
            d[col] = None if col in ("bucket", "regime_prev", "side", "day") else np.nan
    if "notional" not in d:
        d["notional"] = d["entry_px"] * d["qty"]
    d["net_bp"] = d["net_pnl"] / d["notional"] * 1e4
    if day_context:
        d["regime_prev"] = d["day"].map(lambda x: (day_context.get(x) or {}).get("regime_prev"))
        d["vix"] = d["day"].map(lambda x: (day_context.get(x) or {}).get("vix"))
    for name, dim in DIMENSIONS.items():
        d[name] = dim.vector(d) if len(d) else pd.Series(dtype=object)
    return d


def cell_id(strategy: str, dimension: str, value: str) -> str:
    return f"{strategy}|{dimension}|{value}"


def parse_cell(cid: str) -> tuple[str, str, str]:
    s, dim, val = cid.split("|", 2)
    return s, dim, val
