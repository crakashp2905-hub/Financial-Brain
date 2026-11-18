"""Unit tests for the intraday V2 family engine (trial 14): the exit model and causality of every rule."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from financial_brain.evaluation import intraday_v2 as V


def _walk(n_sessions: int, seed: int, vol: float = 0.0015) -> dict:
    rng = np.random.default_rng(seed)
    px = 100.0
    o = np.zeros((n_sessions, V.NB))
    h, lo, c, v = (np.zeros_like(o) for _ in range(4))
    for s in range(n_sessions):
        px *= 1 + rng.normal(0, 0.004)                              # overnight gap
        for j in range(V.NB):
            r = rng.normal(0, vol)
            op, cl = px, px * (1 + r)
            o[s, j], c[s, j] = op, cl
            h[s, j] = max(op, cl) * (1 + abs(rng.normal(0, vol / 2)))
            lo[s, j] = min(op, cl) * (1 - abs(rng.normal(0, vol / 2)))
            v[s, j] = rng.integers(1000, 5000) * (3 if j < 6 else 1)
            px = cl
    days = pd.date_range("2024-01-01", periods=n_sessions, freq="B")
    return {"days": days, "o": o, "h": h, "l": lo, "c": c, "v": v}


def _signals(A: dict, i: int) -> dict:
    B = V.to15(A)
    IND = V.indicators15(B)
    SV = V.slot_volume_mean(A["v"])
    dc = A["c"][:, -1]
    ctx = {"pc": dc[i - 1], "ph": A["h"][i - 1].max(), "pl": A["l"][i - 1].min(),
           "sma20": dc[i - 20: i].mean(), "regime": "RISK_ON",
           "nifty": {"o0": A["o"][i, 0], "o30": A["o"][i, 3], "c1015": A["c"][i, V.J_1015], "c": A["c"][i],
                     "trend": 1.0}}
    return V.evaluate_day(V.Day(A, i, B, IND, SV, ctx))


def test_every_registered_rule_is_callable_and_counts_match() -> None:
    assert len(V.FAMILY) == 35
    assert set(V.CONTROLS) == {"L0", "G0"}
    assert set(V.UNTESTABLE) == {"L14", "L16", "L17", "L19", "L20"}
    # 35 evaluated + 5 untestable = the registered family of forty
    assert len(V.FAMILY) + len(V.UNTESTABLE) == 40


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_no_rule_reads_the_future(seed: int) -> None:
    """Replacing every bar after ``t`` (this session and all later ones) with unrelated data must not change any signal
    that completes at or before ``t``."""
    A = _walk(40, seed, vol=0.003)
    i = 30
    base = _signals(A, i)
    seen_any = False
    for t in (14, 25, 40, 55):
        B = {k: (v.copy() if isinstance(v, np.ndarray) else v) for k, v in A.items()}
        other = _walk(40, seed + 100, vol=0.003)
        for k in "ohlcv":
            B[k][i, t + 1:] = other[k][i, t + 1:] * (B["c"][i, t] / other["c"][i, t])
            B[k][i + 1:] = other[k][i + 1:]
        # keep the session's own OHLC internally consistent
        B["h"][i, t + 1:] = np.maximum(B["h"][i, t + 1:], np.maximum(B["o"][i, t + 1:], B["c"][i, t + 1:]))
        B["l"][i, t + 1:] = np.minimum(B["l"][i, t + 1:], np.minimum(B["o"][i, t + 1:], B["c"][i, t + 1:]))
        alt = _signals(B, i)
        f = {r: (s["j"], s["dir"]) for r, s in base.items() if s["j"] <= t}
        g = {r: (s["j"], s["dir"]) for r, s in alt.items() if s["j"] <= t}
        seen_any |= bool(f)
        assert f == g, f"future leaked at t={t}: {set(f.items()) ^ set(g.items())}"
    assert seen_any, "test data produced no signal to check"


def _one(o, h, lo, c, e, d, sp, tp, last=V.LAST):
    O, H, L, C = (np.array([x], float) for x in (o, h, lo, c))
    return V.simulate(O, H, L, C, np.array([e]), np.array([d]), np.array([sp]), np.array([tp]), np.array([last]))


def _flat(px=100.0):
    a = np.full(V.NB, px)
    return a.copy(), a.copy(), a.copy(), a.copy()


def test_target_hit_long() -> None:
    o, h, lo, c = _flat()
    h[10] = 100.6                                               # +0.6%
    g, kx, r = _one(o, h, lo, c, 5, 1, 0.003, 0.006)
    assert r[0] == 2 and kx[0] == 10 and g[0] == pytest.approx(0.006)


def test_stop_hit_short() -> None:
    o, h, lo, c = _flat()
    h[7] = 100.4
    g, kx, r = _one(o, h, lo, c, 5, -1, 0.003, 0.006)
    assert r[0] == 1 and kx[0] == 7 and g[0] == pytest.approx(-0.003)


def test_stop_taken_first_when_a_bar_reaches_both() -> None:
    o, h, lo, c = _flat()
    h[8], lo[8] = 101.0, 99.0
    g, _kx, r = _one(o, h, lo, c, 5, 1, 0.003, 0.006)
    assert r[0] == 1 and g[0] == pytest.approx(-0.003)


def test_gap_through_the_stop_exits_at_the_open() -> None:
    o, h, lo, c = _flat()
    o[9], h[9], lo[9], c[9] = 98.0, 98.2, 97.9, 98.0
    g, _kx, r = _one(o, h, lo, c, 5, 1, 0.003, 0.006)
    assert r[0] == 1 and g[0] == pytest.approx(98.0 / 100 - 1)


def test_time_exit_at_last_bar_close() -> None:
    o, h, lo, c = _flat()
    c[V.LAST] = 100.2
    g, kx, r = _one(o, h, lo, c, 5, 1, 0.01, 0.02)
    assert r[0] == 0 and kx[0] == V.LAST and g[0] == pytest.approx(0.002)


def test_no_target_means_stop_or_time_only() -> None:
    o, h, lo, c = _flat()
    h[10] = 105.0
    g, _kx, r = _one(o, h, lo, c, 5, 1, 0.003, float("nan"))
    assert r[0] == 0 and g[0] == pytest.approx(0.0)


def test_trailing_stop_locks_in_a_gain() -> None:
    o, h, lo, c = _flat()
    h[8], c[8] = 101.5, 101.4
    o[9:], h[9:], lo[9:], c[9:] = 101.3, 101.4, 101.3, 101.3     # holds above the trailed stop (101.0)
    lo[10] = 100.9                                              # then dips through it
    g = V.simulate_trail(o, h, lo, c, 5, 1, 0.005, 0.005, V.LAST, 100.0)
    assert g == pytest.approx(0.01)


def test_stop_floor_applies_to_a_tight_stop() -> None:
    A = _walk(30, 7)
    B = V.to15(A)
    D = V.Day(A, 25, B, V.indicators15(B), V.slot_volume_mean(A["v"]),
              {"pc": 100.0, "ph": 101.0, "pl": 99.0, "sma20": 100.0, "nifty": None, "regime": None})
    s = D.sig(10, 1, D.o[11] * 0.9999)
    assert s["stop_pct"] == V.FLOOR and s["tgt_pct"] == pytest.approx(2 * V.FLOOR)


def test_session_arrays_drop_thin_sessions_and_fill_gaps() -> None:
    ts = pd.date_range("2024-03-04 09:15", periods=375, freq="min", tz="Asia/Kolkata")
    px = 100 + np.cumsum(np.random.default_rng(0).normal(0, 0.02, 375))
    m = pd.DataFrame({"ts": ts, "open": px, "high": px + 0.05, "low": px - 0.05, "close": px, "volume": 100})
    thin = m.iloc[:40].copy()
    thin["ts"] = thin["ts"] + pd.Timedelta(days=1)
    A = V.session_arrays(pd.concat([m, thin], ignore_index=True))
    assert len(A["days"]) == 1 and A["c"].shape == (1, V.NB) and not np.isnan(A["c"]).any()
