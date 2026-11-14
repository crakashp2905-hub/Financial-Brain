"""The prospective protocol: nominate on what was seen, confirm once on what was not, never test the same numbers twice."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from financial_brain.learning import calibrate as C
from financial_brain.learning import features as F
from financial_brain.learning import protocol as P
from financial_brain.learning.store import Store, fold

CELL = F.cell_id("ORB", "vs_market_today", "against the market")


def _events_to_lessons(events):
    return fold({"lesson_id": e["lesson_id"], "kind": e["kind"], "session": e["session"], "payload": e["payload"]} for e in events)


def _replay(df: pd.DataFrame, params: P.Params = P.DEFAULT, until: int | None = None):
    sessions = sorted(df["day"].unique())
    cm = P.CellMatrix(df, sessions)
    events, lessons = [], {}
    for i, s in enumerate(sessions[:until]):
        if i + 1 < params.min_days:
            continue
        events += P.step(cm, df, s, lessons, params)
        lessons = _events_to_lessons(events)
    return events, lessons, cm, sessions


def _planted(n=70, seed=1, bp=45.0):
    return C.synthetic_ledger(n, seed=seed, plant=("vs_market_today", "against the market", bp))


# ----------------------------------------------------------------------------------------------- the statistics

def test_the_matrix_matches_a_plain_pandas_computation():
    df = C.synthetic_ledger(40, seed=3, plant=("vs_market_today", "against the market", 30.0))
    sessions = sorted(df["day"].unique())
    cm = P.CellMatrix(df, sessions)
    st = cm.stats(hi=len(sessions) - 1).set_index("cell")
    sub = df[(df["strategy"] == "ORB") & (df["vs_market_today"] == "against the market")]
    own = sub.groupby("day")["net_bp"].mean()
    other = df[(df["strategy"] == "ORB") & (df["vs_market_today"] != "against the market")].groupby("day")["net_bp"].mean()
    contrast = (own - other).dropna()
    r = st.loc[CELL]
    assert r["trades"] == len(sub) and r["days"] == len(own)
    assert r["mean_bp"] == pytest.approx(own.mean()) and r["contrast"] == pytest.approx(contrast.mean(), rel=0.2)
    assert r["contrast_days"] > 30 and r["p"] < 0.01


def test_sessions_window_is_exclusive_of_the_nomination_session():
    df = C.synthetic_ledger(30, seed=2)
    sessions = sorted(df["day"].unique())
    cm = P.CellMatrix(df, sessions)
    a = cm.stats(hi=29)
    b = cm.stats(hi=29, lo=cm.lo_after(sessions[9]))                 # sessions 10..29 only
    c = cm.stats(hi=9)
    assert (a["trades"].to_numpy() == b["trades"].to_numpy() + c["trades"].to_numpy()).all()


# ----------------------------------------------------------------------------------------------- the protocol

def test_a_real_effect_is_nominated_then_confirmed_on_fresh_sessions_only():
    events, lessons, cm, sessions = _replay(_planted())
    kinds = [(e["kind"], lessons[e["lesson_id"]].get("cell")) for e in events]
    assert ("NOMINATED", CELL) in kinds and ("CONFIRMED", CELL) in kinds
    lid = next(e["lesson_id"] for e in events if e["kind"] == "NOMINATED" and e["payload"]["cell"] == CELL)
    nom = next(e for e in events if e["lesson_id"] == lid and e["kind"] == "NOMINATED")
    conf = next(e for e in events if e["lesson_id"] == lid and e["kind"] == "CONFIRMED")
    d0 = nom["payload"]["d0"]
    assert conf["payload"]["sessions_after"] >= P.DEFAULT.confirm_sessions
    # the confirmation statistic is exactly the sessions after the nomination: recompute it independently
    new = [s for s in sessions if s > d0 and s <= conf["session"]]
    fresh = cm.stats(hi=cm.pos[conf["session"]], lo=cm.lo_after(d0)).set_index("cell").loc[CELL]
    assert conf["payload"]["trades"] == fresh["trades"] and len(new) == conf["payload"]["sessions_after"]
    assert lessons[lid]["stage"] == "CONFIRMED"


def test_one_cause_gives_one_lesson_not_a_dozen_correlated_ones():
    events, lessons, *_ = _replay(_planted(80, seed=4, bp=40.0))
    confirmed = [lessons[e["lesson_id"]]["cell"] for e in events if e["kind"] == "CONFIRMED"]
    real = {CELL, F.cell_id("GAP", "vs_market_today", "against the market")}   # the planted effect is in both strategies
    assert CELL in confirmed and set(confirmed) <= real, confirmed             # and in no bystander cell


def test_an_effect_that_vanishes_after_the_nomination_is_refuted():
    df = _planted(70, seed=5, bp=60.0)
    early = df["day"] <= sorted(df["day"].unique())[11]               # the effect exists only until the first nomination
    base = C.synthetic_ledger(70, seed=5)                             # same contexts, no effect
    df.loc[~early, ["net_pnl", "net_bp"]] = base.loc[~early, ["net_pnl", "net_bp"]].to_numpy()
    events, lessons, *_ = _replay(df)
    cell_events = [(e["kind"]) for e in events if lessons[e["lesson_id"]].get("cell") == CELL]
    assert "NOMINATED" in cell_events and "CONFIRMED" not in cell_events and "REFUTED" in cell_events


def test_noise_is_not_confirmed_across_many_histories():
    confirmed = nominated = 0
    for seed in range(12):
        df = C.null_outcomes(C.synthetic_ledger(70, seed=100 + seed), np.random.default_rng(seed))
        events, *_ = _replay(df)
        nominated += sum(e["kind"] == "NOMINATED" for e in events)
        confirmed += sum(e["kind"] == "CONFIRMED" for e in events)
    assert confirmed == 0 and nominated <= 6


def test_a_decided_cell_is_not_nominated_again_on_the_same_numbers():
    df = C.synthetic_ledger(70, seed=7)
    sessions = sorted(df["day"].unique())
    cm = P.CellMatrix(df, sessions)
    nom = {"cell": CELL, "d0": sessions[30], "batch_size": 1}
    lessons = fold([{"lesson_id": "L-x", "kind": "NOMINATED", "session": sessions[30], "payload": {**nom, "cell": CELL}},
                    {"lesson_id": "L-x", "kind": "REFUTED", "session": sessions[55], "payload": {"kind": "REFUTED"}}])
    ev = P.step(cm, df, sessions[56], lessons)
    assert all(e["payload"].get("cell") != CELL for e in ev if e["kind"] == "NOMINATED")
    # while an open or standing lesson also blocks it
    lessons2 = fold([{"lesson_id": "L-y", "kind": "NOMINATED", "session": sessions[30], "payload": nom}])
    assert all(e["payload"].get("cell") != CELL for e in P.step(cm, df, sessions[40], lessons2) if e["kind"] == "NOMINATED")


def test_a_cell_that_never_collects_trades_expires():
    df = C.synthetic_ledger(90, seed=8)
    sessions = sorted(df["day"].unique())
    rare = ("ORB", "weekday", "Wed")
    cm = P.CellMatrix(df[~((df["weekday"] == "Wed") & (df["day"] > sessions[30]))], sessions)
    nom = {"cell": F.cell_id(*rare), "d0": sessions[30], "batch_size": 1}
    short = P.Params(expire_sessions=50)
    d = P.decide(cm, nom, sessions[-1], short)
    assert d and d["kind"] == "EXPIRED"
    assert P.decide(cm, nom, sessions[40], short) is None             # too early to say anything


def test_a_standing_lesson_that_stops_holding_is_marked_decaying_not_retired():
    df = _planted(100, seed=9, bp=60.0)
    sessions = sorted(df["day"].unique())
    base = C.synthetic_ledger(100, seed=9)
    late = df["day"] > sessions[60]
    df.loc[late, ["net_pnl", "net_bp"]] = base.loc[late, ["net_pnl", "net_bp"]].to_numpy()
    events, lessons, *_ = _replay(df)
    lid = next(e["lesson_id"] for e in events if e["kind"] == "CONFIRMED")
    kinds = [e["kind"] for e in events if e["lesson_id"] == lid]
    assert "DECAYING" in kinds and lessons[lid]["stage"] == "CONFIRMED" and "RETIRED" not in kinds


def test_batch_size_tightens_the_confirmation_threshold():
    df = _planted(70, seed=1, bp=45.0)
    sessions = sorted(df["day"].unique())
    cm = P.CellMatrix(df, sessions)
    d0 = sessions[14]
    one = P.decide(cm, {"cell": CELL, "d0": d0, "batch_size": 1}, sessions[-1])
    many = P.decide(cm, {"cell": CELL, "d0": d0, "batch_size": 50}, sessions[-1])
    assert one["alpha"] == pytest.approx(P.DEFAULT.alpha_confirm) and many["alpha"] == pytest.approx(P.DEFAULT.alpha_confirm / 50)


def test_the_step_is_idempotent_through_the_store():
    df = _planted(40, seed=1, bp=45.0)
    sessions = sorted(df["day"].unique())
    cm = P.CellMatrix(df, sessions)
    with Store(":memory:") as st:
        for _ in range(2):                                           # the same night run twice
            for e in P.step(cm, df, sessions[26], st.lessons()):
                st.append(e["lesson_id"], e["kind"], session=e["session"], payload=e["payload"])
        n1 = len(st.events())
        for e in P.step(cm, df, sessions[26], st.lessons()):
            st.append(e["lesson_id"], e["kind"], session=e["session"], payload=e["payload"])
        assert len(st.events()) == n1 > 0


def test_a_night_sees_only_what_had_happened_by_then():
    """Replaying with the future present in the table gives the same events as a table that stops at the night."""
    df = _planted(60, seed=1, bp=45.0)
    sessions = sorted(df["day"].unique())
    full = P.CellMatrix(df, sessions)
    cut_df = df[df["day"] <= sessions[36]]
    cut = P.CellMatrix(cut_df, sessions[:37])
    a = P.step(full, df, sessions[36], {})
    b = P.step(cut, cut_df, sessions[36], {})
    key = lambda ev: sorted((e["lesson_id"], e["payload"]["trades"], round(e["payload"]["p"], 12)) for e in ev)   # noqa: E731
    assert key(a) == key(b)


def test_the_watch_list_shows_the_cells_closest_to_the_bar_even_when_none_clears_it():
    df = C.synthetic_ledger(30, seed=12, plant=("vs_market_today", "against the market", 18.0))
    sessions = sorted(df["day"].unique())
    cm = P.CellMatrix(df, sessions)
    w = P.watchlist(cm, sessions[-1], n=5)
    assert 1 <= len(w) <= 5 and w["p"].is_monotonic_increasing and (w["mean_bp"] < 0).all() and (w["contrast_bp"] < 0).all()
    assert CELL in set(w["cell"])


def test_the_watch_list_is_stored_and_replaced_each_night():
    df = C.synthetic_ledger(30, seed=12, plant=("vs_market_today", "against the market", 18.0))
    sessions = sorted(df["day"].unique())
    cm = P.CellMatrix(df, sessions)
    with Store(":memory:") as st:
        st.replace_watchlist("2027-02-01", P.watchlist(cm, sessions[-1]))
        n1 = len(st.watchlist())
        st.replace_watchlist("2027-02-02", P.watchlist(cm, sessions[-1], n=2))
        w = st.watchlist()
    assert n1 > 2 and len(w) == 2 and set(w["run_on"]) == {"2027-02-02"}


# ----------------------------------------------------------------------------------------------- day-level cells

REGIME_CELL = F.cell_id("ORB", "regime_prev", "regime RISK_OFF")


def test_a_day_level_cell_is_compared_with_the_other_days_by_welchs_test():
    from scipy import stats as sps
    df = C.synthetic_ledger(60, seed=21, plant=("regime_prev", "regime RISK_OFF", 40.0))
    sessions = sorted(df["day"].unique())
    r = P.CellMatrix(df, sessions).stats(hi=len(sessions) - 1).set_index("cell").loc[REGIME_CELL]
    o = df[df["strategy"] == "ORB"]
    dm, reg = o.groupby("day")["net_bp"].mean(), o.groupby("day")["regime_prev"].first()
    a, b = dm[reg == "regime RISK_OFF"], dm[reg != "regime RISK_OFF"]
    assert r["days"] == len(a) and r["contrast_days"] == len(a)
    assert r["contrast"] == pytest.approx(a.mean() - b.mean()) and r["p"] == pytest.approx(
        sps.ttest_ind(a, b, equal_var=False, alternative="less").pvalue, rel=1e-6)


def test_a_day_level_window_uses_only_the_sessions_in_the_window():
    from scipy import stats as sps
    df = C.synthetic_ledger(60, seed=22, plant=("weekday", "Wed", 50.0))
    sessions = sorted(df["day"].unique())
    cm = P.CellMatrix(df, sessions)
    r = cm.stats(hi=59, lo=cm.lo_after(sessions[29])).set_index("cell").loc[F.cell_id("ORB", "weekday", "Wed")]
    o = df[(df["strategy"] == "ORB") & (df["day"] > sessions[29])]
    dm, wd = o.groupby("day")["net_bp"].mean(), o.groupby("day")["weekday"].first()
    a, b = dm[wd == "Wed"], dm[wd != "Wed"]
    assert r["days"] == len(a) and r["p"] == pytest.approx(sps.ttest_ind(a, b, equal_var=False, alternative="less").pvalue, rel=1e-6)


def test_a_real_regime_effect_is_nominated_and_confirmed_like_any_other():
    df = C.synthetic_ledger(90, seed=23, plant=("regime_prev", "regime RISK_OFF", 45.0))
    events, lessons, *_ = _replay(df)
    confirmed = {lessons[e["lesson_id"]]["cell"] for e in events if e["kind"] == "CONFIRMED"}
    assert REGIME_CELL in confirmed, confirmed


def test_no_dimension_is_silently_unreachable():
    """Every dimension must be able to produce a nomination: a day-level one through the between-days test, the rest within a day."""
    df = C.synthetic_ledger(60, seed=24)
    sessions = sorted(df["day"].unique())
    st = P.CellMatrix(df, sessions).stats(hi=59)
    st = st[st["trades"] >= 30]
    reach = st.groupby("dimension")["contrast_days"].max()
    for name in F.DIMENSIONS:
        assert reach.get(name, 0) >= 8, f"{name} can never be nominated: no comparison is defined for it"


def test_cells_are_nominated_only_at_the_fixed_looks_but_hypotheses_are_decided_every_night():
    df = _planted(60, seed=1, bp=60.0)
    sessions = sorted(df["day"].unique())
    cm = P.CellMatrix(df, sessions)
    looks = [i for i in range(11, 40) if any(e["kind"] == "NOMINATED" for e in P.step(cm, df, sessions[i], {}))]
    assert looks and all((i + 1 - P.DEFAULT.min_days) % P.DEFAULT.look_every == 0 for i in looks)
    off_look = sessions[12]                                              # 13 sessions: not a look
    assert not [e for e in P.step(cm, df, off_look, {}) if e["kind"] == "NOMINATED"]
    every_night = P.Params(look_every=1)
    assert [e for e in P.step(cm, df, off_look, {}, every_night) if e["kind"] == "NOMINATED"]
