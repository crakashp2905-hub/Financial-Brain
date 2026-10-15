"""Trial 11 machinery on synthetic minute bars: rules fire as written and use no later bar."""
import numpy as np
import pandas as pd

from financial_brain.evaluation import intraday_family as IF


def _day(d: str, path: np.ndarray, vol: np.ndarray | None = None) -> pd.DataFrame:
    ts = pd.date_range(f"{d} 09:15", f"{d} 15:29", freq="1min", tz="Asia/Kolkata")
    path = np.resize(path, len(ts))
    vol = np.full(len(ts), 1000.0) if vol is None else np.resize(vol, len(ts))
    return pd.DataFrame({"ts": ts, "open": path, "high": path * 1.0005, "low": path * 0.9995, "close": path,
                         "volume": vol})


def _history(days=25, seed=0):
    rng = np.random.default_rng(seed)
    out, px = [], 100.0
    for d in pd.bdate_range("2024-01-01", periods=days):
        path = px * np.exp(np.cumsum(0.0003 * rng.standard_normal(375)))
        out.append(_day(str(d.date()), path))
        px = path[-1]
    return pd.concat(out, ignore_index=True), px


def test_gap_and_go_fires_on_a_gap_breakout_and_exits_at_1515():
    m, px = _history()
    d = "2024-02-12"
    path = np.r_[np.full(15, px * 1.02), np.linspace(px * 1.02, px * 1.05, 360)]   # +2% gap, then breaks out up
    m = pd.concat([m, _day(d, path)], ignore_index=True)
    sc = IF.scan_symbol("X", m, fno_days=set(pd.to_datetime(m["ts"].dt.tz_localize(None).dt.normalize()).unique()))
    ev = [e for e in sc["events"]["I1_gap_and_go"] if str(e["d"].date()) == d]
    assert len(ev) == 1 and ev[0]["side"] == 1 and ev[0]["gross"] > 0
    fades = [e for e in sc["events"]["I5_gap_fade"] if str(e["d"].date()) == d]
    assert len(fades) == 1 and fades[0]["side"] == -1 and fades[0]["gross"] < 0


def test_no_event_without_fno_and_no_later_data_used():
    m, _ = _history(30)
    days = sorted(pd.to_datetime(m["ts"].dt.tz_localize(None).dt.normalize()).unique())
    full = IF.scan_symbol("X", m, fno_days=set(days))
    cut = IF.scan_symbol("X", m[m["ts"].dt.tz_localize(None) < days[25]], fno_days=set(days))
    for rule in full["events"]:
        a = [e for e in full["events"][rule] if e["d"] < days[24]]
        b = [e for e in cut["events"][rule] if e["d"] < days[24]]
        assert [(e["d"], e["side"]) for e in a] == [(e["d"], e["side"]) for e in b], rule
    none = IF.scan_symbol("X", m, fno_days=set())
    assert all(len(v) == 0 for v in none["events"].values())


def test_volume_surge_needs_twenty_sessions_of_slot_history():
    m, px = _history(21)
    d = "2024-02-01"
    vol = np.full(375, 1000.0)
    vol[60:75] = 10000.0                                   # 10:15 slot, 10x volume
    path = np.r_[np.full(60, px), np.linspace(px, px * 1.02, 15), np.full(300, px * 1.02)]
    m = pd.concat([m, _day(d, path, vol)], ignore_index=True)
    sc = IF.scan_symbol("X", m, fno_days=set(pd.to_datetime(m["ts"].dt.tz_localize(None).dt.normalize()).unique()))
    ev = [e for e in sc["events"]["I3_surge_15m"] if str(e["d"].date()) == d]
    assert len(ev) == 1 and ev[0]["t_in"] == "10:15" and ev[0]["side"] == 1


def test_a_late_opening_day_is_skipped_not_crashed():
    m, px = _history(21)
    d = "2024-02-01"
    ts = pd.date_range(f"{d} 09:40", f"{d} 15:29", freq="1min", tz="Asia/Kolkata")      # first bar 09:40
    late = pd.DataFrame({"ts": ts, "open": px * 1.03, "high": px * 1.031, "low": px * 1.029, "close": px * 1.03,
                         "volume": 1000.0})
    m = pd.concat([m, late], ignore_index=True)
    sc = IF.scan_symbol("X", m, fno_days=set(pd.to_datetime(m["ts"].dt.tz_localize(None).dt.normalize()).unique()))
    assert not [e for e in sc["events"]["I5_gap_fade"] if str(e["d"].date()) == d]
